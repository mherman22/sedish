# SEDISH `hie` stack

The consolidated-server ingest **and** the SQLMesh → FHIR pipeline, wired together with
Kafka — the end-to-end path from the iSantePlus EMRs to SEDISH's MPI (OpenCR) and SHR.

```
iSantePlus EMRs (openmrs, openmrs2)
   │  cdc-reader (binlog CDC)
   ▼
consolidated_db  (MySQL 8, real production schema, *_openmrs keyed on mspp_code,
   │              date_updated stamped per write)
   ├─ cdc-reader also emits one patient-changed event per row ──▶ Kafka topic
   │                                                                 │
   ▼                                                                 ▼  (debounced trigger)
 (SQLMesh reads consolidated_db) ◀───────────────────────  fhir-pipeline
                                                              ├─ sqlmesh run  → fhir.patient/encounter/observation
                                                              └─ loader       → OpenCR /CR (identity) + SHR /SHR (clinical)
```

## Components
- **`projects/consolidated-server/`** — the CDC reader + the production-schema dump it
  loads into `consolidated_db`. The "Consolidé" ingest stand-in.
- **`projects/openmrs-fhir-sqlmesh/`** — git submodule (the authoritative repo). SQLMesh
  models map `consolidated_db` → FHIR; the loader pushes; `loader/run_kafka.py` is the
  Kafka-triggered driver.
- **`hie/`** — this orchestration: the unified stack + `deploy.sh`.

## Services (4)
| Service | Role |
|---|---|
| `consolidated-db` | MySQL 8; loads the 44-table production schema on first boot; also hosts the SQLMesh `fhir`/`ref` output + state schemas |
| `cdc-reader` | EMR binlog → `consolidated_db` (`*_openmrs`, `mspp_code`, `date_updated`); emits `fhir.patient.changed` to Kafka |
| `kafka` | trigger backbone (buffers/replays) |
| `fhir-pipeline` | SQLMesh transform + loader, **Kafka-triggered** (`RUN_MODE=kafka`); replaces the old fhir2 `publisher` |

One MySQL holds both the source (`consolidated_db`) and the transformed (`fhir`/`ref`) schemas — no extra database.

## Why Kafka drives it (not a poll)
The reader already emits a `fhir.patient.changed` event per changed row. `fhir-pipeline`
consumes those as a **trigger**: it debounces a burst and runs one cycle (`sqlmesh run` →
loader). This beats the `run_continuous.sh` poll — no idle work, events buffer/replay if
OpenHIM is down. The event payload is ignored; correctness is the incremental models + the
loader's `date_updated` watermark, so a missed/duplicate event only causes a redundant,
idempotent cycle. Set `RUN_MODE=poll` for the no-Kafka fallback. (Latency ≈ SQLMesh's 5-min
cron floor either way; Kafka buys idle-efficiency + buffering, not sub-cron latency.)

## Deploy
```bash
git clone --recurse-submodules <sedish>        # or: git submodule update --init
cd hie
cp .env.example .env        # optional; defaults match the dev swarm
./deploy.sh                 # builds both images, then docker stack deploy hie
```
Prereqs (already true on the dev swarm): the `mysql_public` + `openhim_public` overlays
exist, OpenHIM channels `/CR/fhir` + `/SHR/fhir` are configured, and the source MySQL has a
replication user (see `projects/consolidated-server/README.md`).

## Verify
```bash
# consolidated rows by facility
docker exec $(docker ps -qf name=hie_consolidated-db) \
  mysql -uroot -pconsolidated consolidated_db -e \
  "SELECT mspp_code, COUNT(*) FROM patient_openmrs GROUP BY mspp_code;"
# pipeline reacting to events
docker service logs -f hie_fhir-pipeline
# landed in the MPI: cross-facility patients sharing a national id -> one golden record
curl -su openshr:openshr 'http://openhim-core:5001/CR/fhir/Patient?identifier=<national-id>'
```

## Teardown
```bash
docker stack rm hie
docker volume rm hie_consolidated-data hie_kafka-data   # wipes data; re-inits schema next boot
```
