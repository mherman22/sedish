"""
OpenMRS-consolidated rows -> FHIR R4 (pure functions, unit-testable).

Implements the SCOPED transform from SEDISH-Consolidated-Source-Specification.pdf
(§6.3, §9, §10): Patient (demographics + MRNs + national_id), Encounter, and
Observation. This is intentionally NOT a re-implementation of the OpenMRS fhir2
module — it covers the bounded resource set the spec asks DIGI to publish.

Resource ids are the OpenMRS `uuid` columns (globally unique), so SHR/OpenCR
writes are idempotent and cross-site duplicates resolve by shared identifiers.
"""

GENDER = {"M": "male", "F": "female", "O": "other"}

# OpenMRS concept-source name -> FHIR system URI. CHARESS to confirm the full set
# (§11). Unknown sources fall back to a local namespace.
CONCEPT_SOURCE_SYSTEM = {
    "CIEL": "https://openconceptlab.org/orgs/CIEL/sources/CIEL",
    "LOINC": "http://loinc.org",
    "SNOMED CT": "http://snomed.info/sct",
    "SNOMED-CT": "http://snomed.info/sct",
    "ICD-10-WHO": "http://hl7.org/fhir/sid/icd-10",
    "RxNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
}
LOCAL_CONCEPT_SYSTEM = "http://isanteplus.org/openmrs/concept"

# national_fingerprint_mapping.statut values that carry a usable national_id (§7.1).
NATIONAL_ID_STATUSES = {"UNIQUE", "DOUBLON"}


def fhir_dt(v):
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def source_to_system(source_name):
    return CONCEPT_SOURCE_SYSTEM.get((source_name or "").strip(), LOCAL_CONCEPT_SYSTEM)


def mrn_system(mrn_system_base, mspp_code, identifier_type):
    """Per-site MRN identifier system. Placeholder until CHARESS provides the
    canonical namespaces (§9, §11)."""
    return f"{mrn_system_base.rstrip('/')}/mrn/{mspp_code}/{identifier_type}"


def build_patient(person, names, addresses, identifiers, mapping, *,
                  mrn_system_base, national_id_system):
    """person/names/addresses/identifiers are rows from *_openmrs; mapping is the
    national_fingerprint_mapping row (or None). Join key handled by caller (§8.1)."""
    res = {
        "resourceType": "Patient",
        "id": person["uuid"],
        "active": not person.get("voided"),
    }

    # name(s) — preferred first (caller orders preferred=1 first)
    fhir_names = []
    for n in names:
        given = [g for g in (n.get("given_name"), n.get("middle_name")) if g]
        entry = {}
        if n.get("prefix"):
            entry["prefix"] = [n["prefix"]]
        # family_name2 appended if present (OpenMRS second family name)
        fam = " ".join([x for x in (n.get("family_name"), n.get("family_name2")) if x])
        if fam:
            entry["family"] = fam
        if given:
            entry["given"] = given
        if entry:
            entry["use"] = "official" if n.get("preferred") else "usual"
            fhir_names.append(entry)
    if fhir_names:
        res["name"] = fhir_names

    g = (person.get("gender") or "").upper()
    res["gender"] = GENDER.get(g, "unknown")
    bd = fhir_dt(person.get("birthdate"))
    if bd:
        res["birthDate"] = bd[:10]
        if person.get("birthdate_estimated"):
            res.setdefault("_birthDate", {})  # marker; extension TODO if needed
    if person.get("dead"):
        res["deceasedBoolean"] = True
        dd = fhir_dt(person.get("death_date"))
        if dd:
            res["deceasedDateTime"] = dd
            res.pop("deceasedBoolean", None)

    # addresses (§6.3)
    fhir_addrs = []
    for a in addresses:
        ad = {"use": "home"}
        line = [x for x in (a.get("address1"), a.get("address2")) if x]
        if line:
            ad["line"] = line
        for src, dst in (("city_village", "city"), ("state_province", "state"),
                         ("county_district", "district"), ("country", "country"),
                         ("postal_code", "postalCode")):
            if a.get(src):
                ad[dst] = a[src]
        if len(ad) > 1:
            fhir_addrs.append(ad)
    if fhir_addrs:
        res["address"] = fhir_addrs

    # identifiers: per-site MRNs (§6.4) + national_id overlay (§3, §7)
    fhir_idents = []
    for i in identifiers:
        fhir_idents.append({
            "use": "official" if i.get("preferred") else "usual",
            "system": mrn_system(mrn_system_base, person["mspp_code"], i.get("identifier_type")),
            "value": i.get("identifier"),
        })
    if mapping and mapping.get("national_id") and mapping.get("statut") in NATIONAL_ID_STATUSES:
        # UNIQUE -> own id; DOUBLON -> shared canonical id (cross-site link in OpenCR).
        # Same attachment either way; OpenCR links records sharing this value (§7.1).
        fhir_idents.append({
            "use": "official",
            "type": {"text": "National FP ID"},
            "system": national_id_system,
            "value": mapping["national_id"],
        })
    if fhir_idents:
        res["identifier"] = fhir_idents
    return res


def build_encounter(enc, patient_uuid, *, class_code="AMB"):
    res = {
        "resourceType": "Encounter",
        "id": enc["uuid"],
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": class_code},
        "subject": {"reference": f"Patient/{patient_uuid}"},
    }
    start = fhir_dt(enc.get("encounter_datetime"))
    if start:
        res["period"] = {"start": start}
    return res


def _codeable(concept_id, resolver):
    """Build a CodeableConcept for a concept_id via the resolver (CIEL codings if
    the reference tables exist, else concept_name display only)."""
    info = resolver(concept_id) if resolver else None
    if not info:
        return {"coding": [{"system": LOCAL_CONCEPT_SYSTEM, "code": str(concept_id)}]}
    cc = {}
    codings = list(info.get("codings") or [])
    # always include the OpenMRS-local coding so nothing is lost
    codings.append({"system": LOCAL_CONCEPT_SYSTEM, "code": str(concept_id),
                    **({"display": info["display"]} if info.get("display") else {})})
    cc["coding"] = codings
    if info.get("display"):
        cc["text"] = info["display"]
    return cc


def build_observation(obs, patient_uuid, enc_uuid, resolver):
    res = {
        "resourceType": "Observation",
        "id": obs["uuid"],
        "status": "final",
        "code": _codeable(obs.get("concept_id"), resolver),
        "subject": {"reference": f"Patient/{patient_uuid}"},
    }
    eff = fhir_dt(obs.get("obs_datetime"))
    if eff:
        res["effectiveDateTime"] = eff
    if enc_uuid:
        res["encounter"] = {"reference": f"Encounter/{enc_uuid}"}
    # value lives in exactly one column (§10.1)
    if obs.get("value_coded") is not None:
        res["valueCodeableConcept"] = _codeable(obs["value_coded"], resolver)
    elif obs.get("value_numeric") is not None:
        res["valueQuantity"] = {"value": float(obs["value_numeric"])}
    elif obs.get("value_datetime") is not None:
        res["valueDateTime"] = fhir_dt(obs["value_datetime"])
    elif obs.get("value_text") is not None:
        res["valueString"] = obs["value_text"]
    elif obs.get("value_drug") is not None:
        res["valueString"] = f"drug:{obs['value_drug']}"  # TODO: Medication ref when scope decided
    return res


def transaction_bundle(resources):
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": r, "request": {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"}}
            for r in resources if r.get("resourceType") and r.get("id")
        ],
    }


# --------------------------------------------------------------------------
# Self-test: run `python mapping.py` to validate the transform on sample rows
# without any DB. Mirrors the spec's "resolved patient" shape (Annex B).
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import json

    def fake_resolver(cid):
        return {"display": "Health facility where mother received prenatal care",
                "codings": [{"system": CONCEPT_SOURCE_SYSTEM["CIEL"], "code": "163529",
                             "display": "Health facility..."}]} if cid == 163529 else \
               {"display": f"concept {cid}", "codings": []}

    person = {"uuid": "p-uuid-1", "mspp_code": "11106", "gender": "M",
              "birthdate": "1990-01-01", "voided": 0, "dead": 0}
    names = [{"given_name": "Jean", "middle_name": None, "family_name": "Baptiste",
              "family_name2": None, "preferred": 1, "prefix": None}]
    addrs = [{"address1": "Anba Canal", "city_village": "Dessalines",
              "state_province": "Artibonite", "country": "Haiti"}]
    idents = [{"identifier": "ST00160", "identifier_type": 3, "preferred": 1}]
    mapping = {"national_id": "HT-00001830", "statut": "DOUBLON"}
    enc = {"uuid": "e-uuid-1", "encounter_datetime": "2026-01-02T09:00:00"}
    obs = [{"uuid": "o-uuid-1", "concept_id": 163529, "obs_datetime": "2026-01-02T09:05:00",
            "value_text": "Some facility", "value_coded": None, "value_numeric": None,
            "value_datetime": None, "value_drug": None}]

    pat = build_patient(person, names, addrs, idents, mapping,
                        mrn_system_base="http://sedish.ht", national_id_system="http://sedish.ht/national-id")
    bundle = transaction_bundle([pat, build_encounter(enc, person["uuid"])] +
                                [build_observation(o, person["uuid"], enc["uuid"], fake_resolver) for o in obs])
    print(json.dumps(bundle, indent=2))
