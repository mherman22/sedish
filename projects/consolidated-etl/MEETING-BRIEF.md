# Consolidated → SHR/OpenCR ETL — Meeting Brief (DIGI)

**Re:** CHARESS → DIGI handoff (`SEDISH-Consolidated-Source-Specification.pdf`).
**Status:** ETL built and **proven end-to-end against a stand-in** of the CHARESS schema. Awaiting access + a few CHARESS decisions to run on real data.

## The approach (and why)
The consolidated server is a **plain MySQL DB** (OpenMRS-shaped tables + `mspp_code`), **not a booted OpenMRS**. So `fhir2` / `fhir-data-pipes` can't run on it — they need a live OpenMRS app. We therefore built a **scoped ETL**: extract → transform to FHIR → load **identity → OpenCR**, **clinical → SHR** (via OpenHIM). This is a bounded transform (Patient / Encounter / Observation), **not** a re-implementation of the fhir2 module.

```
consolidated_db ──ETL──▶ OpenCR (identity, dedup)   +   SHR (clinical), via OpenHIM
```

## What's done and verified
Ran the ETL against a stand-in (2 sites, composite-key collision, a DOUBLON, an A_REVOIR) into the live OpenCR + SHR — **fully green**:
- ✅ Resolve patients on the composite key **`(mspp_code, patient_id)`**; `uuid` as resource id; `voided`/`preferred` filters.
- ✅ FHIR Patient (demographics + per-site MRNs + national_id), Encounter, Observation (concept labels).
- ✅ **All patients enrolled in OpenCR.**
- ✅ **DOUBLON cross-site dedup:** the two site records sharing `HT-00001830` linked into **one golden record** (spec §7.1).
- ✅ Clinical (Encounters + Observations) landed in the SHR.
- ✅ Incremental, idempotent, batched by `mspp_code`; transient errors retried.

## Three integration findings (concrete, from the run)
1. **OpenCR is strict on identifier systems** — it rejects any Patient lacking an identifier in its configured `internalid` systems (the `…/fhir2/3-isanteplus-id` family). The ETL's identifier systems **must match OpenCR's `config.json`**.
2. **DOUBLON dedup only works** when `national_id` is in a system OpenCR matches on (we used the **biometrics** system → OpenCR's biometric rule links the duplicates).
3. **SHR HAPI** must allow placeholder references (`auto_create_placeholder_reference_targets=true` / `enforce_referential_integrity_on_write=false`, set by `post-deploy.sh`) — else it rejects the mediator's golden-record `Patient.link`.

## What we need from CHARESS (to go live)
1. **Read-only credentials + VPN / IP allow-list** (host `54.200.60.231:3310`).
2. **FHIR system URIs** for per-site MRNs and the national_id (must align with OpenCR config — finding #1/#2).
3. **Are the CIEL reference-map tables present?** (`concept_reference_map/_term/_source`) → decides real **codings** vs `concept_name` **labels** (fidelity vs the live-EMR SHR data).
4. **Clinical scope:** core obs/encounters only, or also the iSantePlus domain tables (labs, ARV, TB, pregnancy, vaccination…); raw obs vs pre-shaped.
5. **Conflict policy** (biometric vs demographic precedence) + **load cadence**.
6. **Timing:** schema is stable so ETL dev proceeds now; **production extraction waits for the dedup run to finish** (mapping not final), and the loader is **incremental** (national IDs keep getting attached as coverage grows).

## Bottom line
The hard, SEDISH-specific parts are working and proven (composite keys, OpenCR enrollment + DOUBLON dedup, clinical to SHR). Remaining work is **access + the decisions above**, then run on real data starting with a dry run.

*Code: `projects/consolidated-etl/` on branch `feat/consolidated-pipeline`.*
