# Consolidated Server (prototype)

A faithful, self-contained stand-in for the production **Consolidé** server. It
reproduces how the real consolidated server ingests data: by reading the
**MySQL binlog** of the iSantePlus facility databases (Change Data Capture) and
storing a merged copy in its own MySQL.

This is **phase 1** (prove the CDC ingest). Phase 2 — pushing the consolidated
data into the **SHR** — is intentionally not built yet (see *Next steps*).

```
iSantePlus MySQL (openmrs, openmrs2, … ROW binlog)
        │  replication client (pymysqlreplication, server_id=100001)
        ▼
   cdc-reader  ──upsert──▶  consolidated-db (MySQL)
                              tables keyed on (_source_db, <orig PK>)
```

## Why these choices

- **`python-mysql-replication`** — the real Consolidé is a "Script Py" that
  watches the binlog; this is the canonical Python way to do that. One small
  container, no Kafka/Debezium to operate.
- **Composite PK `(_source_db, …)`** — 10 facility DBs share primary keys
  (`patient_id=1` exists in each), so the source DB name is part of the key to
  merge them without collisions.
- **Clinical-core tables** — `person, person_name, person_address, patient,
  patient_identifier, encounter, visit, obs` — the set that maps to FHIR
  Patient/Encounter/Observation for the eventual SHR push.

## Prerequisites (one-time): replication user on the source MySQL

The reader connects as a replication client, so it needs a user with
`REPLICATION SLAVE`, `REPLICATION CLIENT`, and `SELECT` (for the initial
snapshot + schema introspection):

```sql
CREATE USER IF NOT EXISTS 'consolidated'@'%' IDENTIFIED BY 'consolidated';
GRANT REPLICATION SLAVE, REPLICATION CLIENT, SELECT ON *.* TO 'consolidated'@'%';
FLUSH PRIVILEGES;
```

The source binlog is already ROW-format with `server-id=223344`
(`packages/database-mysql/config/mysql.cnf`); the reader uses a different
`server_id` (100001).

## Run

```bash
cp .env.example .env   # optional; defaults work for the dev stack
./run.sh               # builds the image and deploys the swarm stack
docker service logs -f consolidated_cdc-reader
```

## Verify

```bash
# how many patients did we consolidate, and from which facilities?
docker exec -e MYSQL_PWD=consolidated \
  "$(docker ps -q -f name=consolidated_consolidated-db)" \
  mysql -uroot consolidated -e \
  "SELECT _source_db, COUNT(*) FROM patient GROUP BY _source_db;"
```

Then create/edit a patient in iSantePlus and watch the reader log a
`Write`/`Update` event and the row appear in `consolidated`.

## Teardown

```bash
docker stack rm consolidated
docker volume rm consolidated_consolidated-data   # wipes consolidated data
```

## Next steps (phase 2 — connect to the SHR)

The consolidated data is OpenMRS-shaped relational rows. To reach the SHR
(`http://openhim-core:5001/SHR/fhir`, client `shr-pipeline`) we either:

1. **Reuse fhir-data-pipes in batch mode** pointed at a FHIR view of the
   consolidated DB, or have the consolidated server expose a FHIR endpoint —
   then reuse the existing `/SHR/fhir` sink (least new code; mirrors the
   current EMR→SHR pipeline which already runs `fhirFetchMode: FHIR_SEARCH`).
2. **Add a translator** in this service that maps the consolidated rows to FHIR
   R4 bundles and POSTs them to `/SHR/fhir`, modelled on `projects/lnsp-mediator`.
