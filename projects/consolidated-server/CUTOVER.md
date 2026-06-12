# Cutover checklist — retiring fhir-data-pipes in favour of the consolidated server

Goal: make the consolidated server the **single feed** into the SHR (and MPI),
and turn off the EMR `fhir-data-pipes` pipeline — safely, on evidence.

## Architecture after cutover

```
iSantePlus EMRs (MySQL)
   │  binlog CDC (reader.py, pymysqlreplication)
   ▼
consolidated MySQL  ──emit patient-changed──▶ Kafka (fhir.patient.changed)
                                                  │
                              publisher_kafka.py ─┤ consume
                                                  ├─▶ pull patient FHIR from EMR fhir2
                                                  ├─▶ POST /SHR/fhir  (HTTP, MPI-enriched by mediator) ▶ HAPI
                                                  └─▶ PUT  /CR/fhir   (OpenCR enrollment + dedup)
                              (+ periodic global Practitioner/Location sync)
```

The EMRs continue to push patients to `/CR/fhir` with their own facility client
(this is the existing OpenCR enrollment path — see step 1).

## Pre-cutover gates (must all pass)

1. **Confirm the production OpenCR enrollment path.** ⚠️ The one true blocker.
   In dev, enrollment is manual/migration; in prod the OpenCR records are tagged
   with facility client ids (`lapaix`, `hueh`, …), i.e. the **EMR OpenMRS pushes
   patients to `/CR/fhir`** independently of fhir-data-pipes. Verify this is true
   in prod (check the OpenMRS client-registry/MPI integration on each EMR, and
   that those clients still post after the pipeline is off). The consolidated
   `publisher_kafka` also enrolls to `/CR/fhir`, so this is belt-and-braces — but
   confirm at least one path enrolls new patients.

2. **Parity is green.** Run the parity harness; expect zero MISSING:
   ```bash
   docker exec $(docker ps -q -f name=consolidated_publisher) python -u parity.py
   ```
   Re-run after creating/editing patients across facilities.

3. **Resource coverage matches** the old pipeline's `resourceList`:
   Patient, Encounter, Observation, Condition, AllergyIntolerance, MedicationRequest
   (patient-scoped, via `publisher_kafka`) + Practitioner, Location (global sync).
   If the SHR consumers need `Group`/`Practitioner`/`Location` beyond what's
   synced, add them to `GLOBAL_RESOURCES` / `PATIENT_RESOURCES`.

4. **Resilience check.** Stop the SHR (or block `/SHR/fhir`), make EMR changes,
   confirm events buffer in Kafka, then bring the SHR back and confirm the
   backlog drains (offsets commit, resources land). Confirms Pattern A buffering.

5. **Backfill.** New Kafka topic only has events from when the reader connected.
   For a one-time full load, either run the poll-mode publisher once
   (`publisher_fhir2.py` with `FORCE_FULL=1`) or re-snapshot so the reader
   re-emits. Verify counts in the SHR.

## Cutover steps

1. Deploy/verify the consolidated stack is healthy (reader 1/1, kafka 1/1,
   publisher 1/1, consolidated-db 1/1) and gates 1–5 pass.
2. Scale the EMR pipeline to 0:
   ```bash
   docker service scale pipeline_pipeline-isanteplus1=0 pipeline_pipeline-isanteplus2=0
   ```
3. Make a change in each EMR; confirm it reaches the SHR via the consolidated
   route only (publisher logs + parity).
4. Monitor for one full cycle (latency, error rate, Kafka consumer lag).

## Rollback

- Scale the EMR pipeline back up: `docker service scale pipeline_pipeline-isanteplus1=1 pipeline_pipeline-isanteplus2=1`.
- Both routes are idempotent (same OpenMRS uuids → PUT), so running both
  simultaneously is safe and creates no duplicates — rollback is non-destructive.

## Known gaps / follow-ups

- Bare/invalid patients are rejected by OpenCR (`Invalid patient resource`) — expected.
- Kafka producer in the reader is best-effort; the consolidated DB is the durable
  store. For strict exactly-the-event delivery, add an outbox/transactional emit.
- Fingerprint *template* data (M2Sys) beyond the biometric reference-code
  identifier is not handled; matching relies on the biometric identifier OpenCR
  already dedups on.
- Single-node Kafka (KRaft) — fine for a prototype; cluster for prod.
