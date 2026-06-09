# Consolidated → FHIR ETL (DIGI)

The DIGI side of the **CHARESS → DIGI** handoff
(`SEDISH-Consolidated-Source-Specification.pdf`): extract patient data from the
CHARESS **`consolidated_db`**, transform it to FHIR, and load **identity → OpenCR**
and **clinical → SHR**, via OpenHIM.

```
consolidated_db (MySQL 8, read-only, CHARESS)
   │  resolve by composite key (mspp_code, patient_id)   [§8.1]
   ▼
 ETL ──Patient (+MRNs +national_id)──► OpenCR  /CR/fhir     (identity / MPI)
     └─Patient + Encounter + Observation──► SHR  /SHR/fhir  (clinical)
```

## Why this shape (not fhir2 / not fhir-data-pipes)
The consolidated server is a **plain MySQL DB, not a booted OpenMRS** — it's a
partitioned, multi-site merge of OpenMRS tables (`*_openmrs` + `mspp_code`). So
fhir2/fhir-data-pipes (which need a running OpenMRS) can't run on it, and the
transform is ours. This is a **scoped** transform (Patient/Encounter/Observation),
**not** a re-implementation of the OpenMRS fhir2 module.

## Key rules from the spec, implemented here
- **Composite key** `(mspp_code, patient_id)`; `person_id = patient_id` (§6.1, §8.1).
- **Resource ids = OpenMRS `uuid`** → idempotent PUT, cross-site dedup by identifiers.
- **`voided=0`** filtered; **`preferred=1`** preferred for name/MRN.
- **`national_fingerprint_mapping`** (§7): `national_id` attached only for
  `statut ∈ {UNIQUE, DOUBLON}`; **DOUBLON reuses the original's `national_id`**
  (shared id = OpenCR cross-site link; no new golden record). ~94% have none → demographic matching.
- **All patients → OpenCR**; clinical → SHR linked to the same Patient (§2, §3).
- **Incremental & idempotent**, batched by `mspp_code` (§8.3); per-site watermark
  kept locally (source is read-only).
- **Concept resolution** (§10.1): `concept_name` for the display label; CIEL/LOINC
  codings **if** the `concept_reference_map/_term/_source` tables exist — otherwise
  label-only (graceful). See open item below.

## Files
- `mapping.py` — pure OpenMRS-row → FHIR functions (Patient/Encounter/Observation). Unit-testable.
- `db.py` — read-only queries + the §8.1 resolved-patient join + `ConceptResolver`.
- `state.py` — per-site incremental watermark (local JSON; source is read-only).
- `etl.py` — orchestration: per site → changed patients → build → OpenCR + SHR.

## Try the transform without a DB
```bash
python mapping.py     # prints a sample FHIR transaction bundle from fabricated rows
```

## Run (once access/VPN is provided)
```bash
cp .env.example .env      # fill SRC_* with CHARESS read-only creds
docker build -t consolidated-etl:local .
docker stack deploy -c docker-compose.yml consolidated-etl
# safe first pass: set DRY_RUN=1 to build FHIR without pushing
```

## Open items to confirm with CHARESS (drive fidelity/scope — §9–§11)
1. **CIEL reference-map tables present?** (`concept_reference_map/_term/_source`) — decides codings vs labels.
2. **FHIR system URIs** for per-site MRNs and the national_id.
3. **Clinical scope:** core obs/encounters only, or also the iSantePlus domain
   tables (dispensing, labs, ARV, TB, pregnancy, vaccination…); raw obs vs pre-shaped.
4. **Conflict policy** (biometric vs demographic precedence) + **load cadence**.
5. **Access:** read-only creds + VPN/allow-list; production load waits for the
   dedup run to finish (mapping not final) — dev runs against the schema now (§12).
