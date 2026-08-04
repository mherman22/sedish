# Consolidé: missing patient identifiers — findings for CHARESS (2026-08-04)

**Symptom:** patients fed into the Client Registry from the consolidated DB sometimes show no
iSantePlus ID / Code National — only the SEDISH source-key.

We traced the full chain (Consolidé → sync → SQLMesh transform → OpenCR) and verified every DIGI
component end-to-end: the transform resolves identifier types by name per facility, the mapped
identifiers survive into the FHIR output, and live registry records written by the batch feed carry
iSantePlus ID, Code National, Code ST and Code PC (e.g. source-key `73106-31` carries all of them).
The gaps are in the consolidated DB itself, verified **directly against the Consolidé server**
(`digi_ro`, read-only) on 2026-08-04 — two distinct issues:

## 1. Site 75101 (Ste Anne): `patient_identifier` replication stalled since 23 July

| site | newest `patient_identifier` row | newest `person` row |
|---|---|---|
| 73106 | 2026-08-03 19:41 | 2026-08-03 19:41 |
| 54111 | 2026-08-03 19:43 | 2026-08-03 19:43 |
| **75101** | **2026-07-23 14:32** | 2026-08-03 19:26 |

Persons, names and addresses from 75101 still replicate normally; identifier rows stopped on
2026-07-23 (0 rows since). Every patient registered at 75101 after that date exists in Consolidé
with demographics but **no identifiers**, so their registry records show only the source-key.

**Ask:** repair the `patient_identifier` feed from site 75101 and backfill from 2026-07-23. No
action needed on the DIGI side afterwards — the ETL detects the late-arriving identifier rows
(change detection includes the identifier watermark) and re-emits those patients automatically;
the registry reconciler appends the missing identifiers to the existing records.

## 2. Non-demo sites: `patient_identifier_type` dimension never loaded

`patient_identifier_type` contains rows **only** for 73106, 54111 and 75101 (16 types each). The
eight other sites (11001, 11002, 21100, 22101, 31200, 41300, 51400, 61500) have none — and their
`patient_identifier` data is exactly one row per patient, all with raw `identifier_type = 5`.

Without the per-facility type dimension, those identifiers cannot be classified (type ids are not
stable across facilities, so a positional guess would mislabel them — e.g. an iSantePlus ID surfacing
as Code PC). The ETL therefore drops them deliberately rather than emit unlabelled values, and those
sites' registry records carry only the source-key + the fingerprint national ID.

**Ask:** (a) load the `patient_identifier_type` rows for the non-demo sites; (b) check whether those
sites' identifier replication is itself partial — exactly one identifier per patient suggests a
single type is being fed, whereas the demo sites average 3–4 per patient (iSantePlus ID, Code
National, Code ST/PC, …). As above, the ETL heals the registry automatically once the data lands.
