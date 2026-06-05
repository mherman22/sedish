"""
Consolidated-server -> SHR publisher (phase 2).

Reads the consolidated MySQL (populated by reader.py) and pushes its clinical
data to the SEDISH SHR as FHIR R4. For each patient it builds one transaction
Bundle (Patient + that patient's Encounters + Observations) and POSTs it to
OpenHIM's SHR passthrough channel, which routes through the SHR mediator
(golden-record normalisation) into HAPI FHIR.

Resource ids are the OpenMRS `uuid` columns, so:
  * ids are globally unique across facilities,
  * re-runs are idempotent (transaction entries use PUT = update-or-create),
  * the same person seen at two facilities yields two Patient resources that
    OpenCR links into one golden record — exactly like the existing EMR pipeline.

Runs once and exits, or loops every PUBLISH_INTERVAL seconds if that env is set.
"""
import base64
import json
import os
import sys
import time
import logging
import urllib.request
import urllib.error

import pymysql
from pymysql.cursors import DictCursor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("publisher")


def env(name, default=None):
    return os.environ.get(name, default)


DST = {
    "host": env("DST_HOST", "consolidated-db"),
    "port": int(env("DST_PORT", "3306")),
    "user": env("DST_USER", "root"),
    "password": env("DST_PASS", "consolidated"),
    "db": env("DST_DB", "consolidated"),
}
SHR_URL = env("SHR_URL", "http://openhim-core:5001/SHR/fhir").rstrip("/")
SHR_USER = env("SHR_USER", "shr-pipeline")
SHR_PASS = env("SHR_PASS", "instant101")
PUBLISH_INTERVAL = int(env("PUBLISH_INTERVAL", "0"))  # 0 = run once and exit

# OpenMRS patient_identifier_type id -> (FHIR system URI, display). From the
# iSantePlus fhir_patient_identifier_system table (see debugging memory). The
# consolidated DB doesn't carry the type table, so we map by id here.
IDENTIFIER_SYSTEMS = {
    3: ("http://isanteplus.org/openmrs/fhir2/3-isanteplus-id", "iSantePlus ID"),
    4: ("http://isanteplus.org/openmrs/fhir2/6-code-st", "Code ST"),
    5: ("http://isanteplus.org/openmrs/fhir2/5-code-national", "Code National"),
    6: ("http://isanteplus.org/openmrs/fhir2/6-biometrics-national-reference-code", "Biometrics"),
    9: ("http://isanteplus.org/openmrs/fhir2/9-code-pc", "Code PC"),
}
GENDER = {"M": "male", "F": "female", "O": "other"}


def connect_dst():
    return pymysql.connect(charset="utf8mb4", autocommit=True, cursorclass=DictCursor, **DST)


def fhir_dt(v):
    """Format a date/datetime value as a FHIR dateTime/date string, or None."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def rows(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def build_patient(conn, src, person):
    pid = person["person_id"]
    names = rows(conn,
        "SELECT * FROM person_name WHERE _source_db=%s AND person_id=%s "
        "ORDER BY preferred DESC, person_name_id ASC", (src, pid))
    addrs = rows(conn,
        "SELECT * FROM person_address WHERE _source_db=%s AND person_id=%s "
        "ORDER BY preferred DESC, person_address_id ASC", (src, pid))
    idents = rows(conn,
        "SELECT * FROM patient_identifier WHERE _source_db=%s AND patient_id=%s "
        "AND (voided=0 OR voided IS NULL)", (src, pid))

    res = {
        "resourceType": "Patient",
        "id": person["uuid"],
        "active": not person.get("voided"),
    }
    fhir_idents = []
    for i in idents:
        system, display = IDENTIFIER_SYSTEMS.get(
            i.get("identifier_type"),
            (f"urn:isanteplus:identifier-type:{i.get('identifier_type')}", "Unknown"))
        fhir_idents.append({
            "use": "official" if i.get("preferred") else "usual",
            "type": {"text": display},
            "system": system,
            "value": i.get("identifier"),
        })
    if fhir_idents:
        res["identifier"] = fhir_idents

    fhir_names = []
    for n in names:
        given = [g for g in (n.get("given_name"), n.get("middle_name")) if g]
        entry = {}
        if n.get("family_name"):
            entry["family"] = n["family_name"]
        if given:
            entry["given"] = given
        if entry:
            fhir_names.append(entry)
    if fhir_names:
        res["name"] = fhir_names

    g = (person.get("gender") or "").upper()
    res["gender"] = GENDER.get(g, "unknown")
    bd = fhir_dt(person.get("birthdate"))
    if bd:
        res["birthDate"] = bd[:10]
    if person.get("dead"):
        res["deceasedBoolean"] = True

    fhir_addrs = []
    for a in addrs:
        ad = {"use": "home"}
        line = [x for x in (a.get("address1"), a.get("address2")) if x]
        if line:
            ad["line"] = line
        for k_src, k_fhir in (("city_village", "city"), ("state_province", "state"),
                              ("country", "country"), ("postal_code", "postalCode")):
            if a.get(k_src):
                ad[k_fhir] = a[k_src]
        if len(ad) > 1:
            fhir_addrs.append(ad)
    if fhir_addrs:
        res["address"] = fhir_addrs
    return res


def build_encounter(enc, patient_uuid):
    res = {
        "resourceType": "Encounter",
        "id": enc["uuid"],
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
        "subject": {"reference": f"Patient/{patient_uuid}"},
    }
    start = fhir_dt(enc.get("encounter_datetime"))
    if start:
        res["period"] = {"start": start}
    return res


def build_observation(obs, patient_uuid, enc_uuid_by_id):
    res = {
        "resourceType": "Observation",
        "id": obs["uuid"],
        "status": "final",
        "code": {"coding": [{
            "system": "http://isanteplus.org/openmrs/concept",
            "code": str(obs.get("concept_id")),
        }]},
        "subject": {"reference": f"Patient/{patient_uuid}"},
    }
    eff = fhir_dt(obs.get("obs_datetime"))
    if eff:
        res["effectiveDateTime"] = eff
    enc_uuid = enc_uuid_by_id.get(obs.get("encounter_id"))
    if enc_uuid:
        res["encounter"] = {"reference": f"Encounter/{enc_uuid}"}
    # pick whichever value column is populated
    if obs.get("value_numeric") is not None:
        res["valueQuantity"] = {"value": float(obs["value_numeric"])}
    elif obs.get("value_coded") is not None:
        res["valueCodeableConcept"] = {"coding": [{
            "system": "http://isanteplus.org/openmrs/concept",
            "code": str(obs["value_coded"]),
        }]}
    elif obs.get("value_datetime") is not None:
        res["valueDateTime"] = fhir_dt(obs["value_datetime"])
    elif obs.get("value_text") is not None:
        res["valueString"] = obs["value_text"]
    return res


def bundle_for_patient(conn, src, person):
    patient_uuid = person["uuid"]
    pid = person["person_id"]
    entries = [(("Patient", patient_uuid), build_patient(conn, src, person))]

    encs = rows(conn,
        "SELECT * FROM encounter WHERE _source_db=%s AND patient_id=%s "
        "AND (voided=0 OR voided IS NULL)", (src, pid))
    enc_uuid_by_id = {e["encounter_id"]: e["uuid"] for e in encs}
    for e in encs:
        entries.append((("Encounter", e["uuid"]), build_encounter(e, patient_uuid)))

    obses = rows(conn,
        "SELECT * FROM obs WHERE _source_db=%s AND person_id=%s "
        "AND (voided=0 OR voided IS NULL)", (src, pid))
    for o in obses:
        entries.append((("Observation", o["uuid"]), build_observation(o, patient_uuid, enc_uuid_by_id)))

    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "fullUrl": f"{rtype}/{rid}",
                "resource": res,
                "request": {"method": "PUT", "url": f"{rtype}/{rid}"},
            }
            for (rtype, rid), res in entries
        ],
    }, len(encs), len(obses)


def post_bundle(bundle):
    data = json.dumps(bundle).encode("utf-8")
    auth = base64.b64encode(f"{SHR_USER}:{SHR_PASS}".encode()).decode()
    req = urllib.request.Request(
        SHR_URL, data=data, method="POST",
        headers={
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json",
            "Authorization": f"Basic {auth}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def publish_once(conn):
    patients = rows(conn,
        "SELECT p.* FROM person p JOIN patient pt "
        "ON pt._source_db=p._source_db AND pt.patient_id=p.person_id")
    log.info("publishing %d patients to %s", len(patients), SHR_URL)
    ok = fail = 0
    for person in patients:
        src = person["_source_db"]
        bundle, n_enc, n_obs = bundle_for_patient(conn, src, person)
        try:
            status, _ = post_bundle(bundle)
            ok += 1
            log.info("[%s] Patient/%s -> SHR %s (%d enc, %d obs)",
                     src, person["uuid"], status, n_enc, n_obs)
        except urllib.error.HTTPError as e:
            fail += 1
            log.error("[%s] Patient/%s FAILED %s: %s",
                      src, person["uuid"], e.code, e.read().decode("utf-8", "replace")[:500])
        except Exception as e:  # noqa: BLE001
            fail += 1
            log.error("[%s] Patient/%s FAILED: %s", src, person["uuid"], e)
    log.info("publish run complete: %d ok, %d failed", ok, fail)
    return ok, fail


def main():
    while True:
        try:
            conn = connect_dst()
        except Exception as e:  # noqa: BLE001
            log.info("waiting for consolidated MySQL (%s)", e)
            time.sleep(3)
            continue
        try:
            publish_once(conn)
        finally:
            conn.close()
        if PUBLISH_INTERVAL <= 0:
            return
        log.info("sleeping %ds until next publish run", PUBLISH_INTERVAL)
        time.sleep(PUBLISH_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
