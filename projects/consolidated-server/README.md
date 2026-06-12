# Consolidated Server (prototype)

A faithful, self-contained stand-in for the production **Consolidé** server. It
reproduces how the real consolidated server ingests data: by reading the
**MySQL binlog** of the iSantePlus facility databases (Change Data Capture) and
storing a merged copy in its own MySQL.

This is **phase 1** (CDC ingest into the production schema). Phase 2 — mapping
the consolidated data to FHIR and pushing it to **OpenCR + SHR** — is the
separate **`openmrs-fhir-sqlmesh`** pipeline, which reads this `consolidated_db`
(that's why this now uses the real schema). See *Next steps*.

The consolidated MySQL is **built on the real production schema** — the dump
`schema/consolidated_db_schema.sql` (44 tables, `consolidated_db`, MySQL 8) —
loaded on first boot. So the reader writes into the actual production tables
(`person_openmrs`, `obs_openmrs`, …) keyed on `mspp_code`, exactly as the real
Consolidé does, and the downstream SQLMesh/FHIR pipeline runs against a faithful
replica rather than a made-up schema.

```
iSantePlus MySQL (openmrs, openmrs2, … ROW binlog)
        │  replication client (pymysqlreplication, server_id=100001)
        ▼
   cdc-reader  ──upsert──▶  consolidated-db  (MySQL 8, production schema)
        │                     person → person_openmrs, obs → obs_openmrs, …
        │                     facility db → mspp_code; date_updated stamped per write
```

## Why these choices

- **`python-mysql-replication`** — the real Consolidé is a "Script Py" that
  watches the binlog; this is the canonical Python way to do that. One small
  container, no Kafka/Debezium to operate.
- **Production schema, keyed on `mspp_code`** — the consolidated tables ARE the
  production tables (`*_openmrs`), where each facility is distinguished by its
  site code `mspp_code` (the tables are `PARTITION BY RANGE(year(date_created))
  SUBPARTITION BY KEY(mspp_code)`, PK includes `mspp_code` + `date_created`).
  `SCHEMA_MSPP` maps each facility database to its code (e.g.
  `openmrs=11106,openmrs2=22207`). Every write stamps `date_updated` — the
  watermark the downstream incremental SQLMesh pipeline reads.
- **Clinical-core tables** — `person, person_name, person_address, patient,
  patient_identifier, encounter, visit, obs` — the set that maps to FHIR
  Patient/Encounter/Observation for the SHR push.

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
# how many patients did we consolidate, and from which facilities (mspp_code)?
docker exec -e MYSQL_PWD=consolidated \
  "$(docker ps -q -f name=consolidated_consolidated-db)" \
  mysql -uroot consolidated_db -e \
  "SELECT mspp_code, COUNT(*) FROM patient_openmrs GROUP BY mspp_code;"
```

Then create/edit a patient in iSantePlus and watch the reader log a
`Write`/`Update` event and the row appear in `consolidated`.

## Teardown

```bash
docker stack rm consolidated
docker volume rm consolidated_consolidated-data   # wipes data AND re-inits the schema next boot
```

## Next steps (phase 2 — to OpenCR + SHR)

Phase 2 is the **`openmrs-fhir-sqlmesh`** pipeline (SQLMesh maps `consolidated_db`
→ FHIR; a loader pushes Patients to OpenCR `/CR/fhir` and clinical transaction
bundles to the SHR `/SHR/fhir`). Because this server now uses the real
`consolidated_db` schema, that pipeline runs against it directly — incrementally,
keyed on the `date_updated` this reader stamps.

The Kafka backbone here (`fhir.patient.changed`) can also drive that pipeline
event-by-event instead of on a timer (the `publisher` service is the CDC-triggered
variant).
