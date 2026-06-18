# data-pipeline-consolidated-server

> Full end-to-end setup (both packages, deploy, verify, troubleshooting):
> [`docs/consolidated-pipeline-setup.md`](../../docs/consolidated-pipeline-setup.md)

Maps the CHARESS **Consolidé** `consolidated_db` to FHIR R4 with SQLMesh and routes it through
OpenHIM — Patient identities to **OpenCR**, clinical to the **SHR** (via the `fhir-router-mediator`).
The pipeline code/image is the separate repo `github.com/mherman22/sedish-fhir-pipeline`; this
package is the deploy wiring.

## Two modes

The same image runs either way; the mode is chosen by whether `SRC_*` is set (the compose handles it).

| | **SYNC** (default) | **DIRECT** |
|---|---|---|
| When | Consolidé is **read-only** to us | We have **write** access on Consolidé |
| How | a local `pipeline-db` holds a synced copy of `consolidated_db`; SQLMesh transforms there | SQLMesh runs on Consolidé itself; no copy |
| Consolidé grant | `SELECT` on `consolidated_db` | `SELECT` + `ALL` on `fhir`/`sqlmesh`/`sqlmesh__fhir` |
| Compose | `docker-compose.yml` | `docker-compose.direct.yml` |
| Local DB | `pipeline-db` service | none |

SYNC is the default because it needs nothing from CHARESS beyond read access. DIRECT avoids the copy
(better at scale) but requires the write grant + pre-created schemas.

## SYNC mode (default)

**`.env`** — `CONSOLIDATED_*` is the read-only Consolidé user:
```bash
CONSOLIDATED_HOST=<consolidé-host>
CONSOLIDATED_PORT=3306
CONSOLIDATED_USER=<read-only-user>
CONSOLIDATED_PASS=<password>
PIPELINE_DB_PW=pipeline        # local pipeline-db root password
PIPELINE_IMAGE=ghcr.io/mherman22/sedish-fhir-pipeline:main
```
`FHIR_DB_*` and `SRC_*` are wired by the compose. Steady state copies only changed rows; a periodic
full reconcile (`SYNC_RECONCILE_EVERY`, default 1h) catches edits/deletes. New patients are caught
via `date_created`.

**Deploy:**
```bash
./build-image.sh
./instant package init -n data-pipeline-consolidated-server --env-file .env
```

## DIRECT mode (write access)

**Prereq — CHARESS pre-creates 3 schemas + grants the user** (no global `CREATE`, no `fhir_test`):
```sql
CREATE DATABASE fhir;  CREATE DATABASE sqlmesh;  CREATE DATABASE sqlmesh__fhir;
GRANT SELECT         ON consolidated_db.*  TO '<user>'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON fhir.*             TO '<user>'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON sqlmesh.*          TO '<user>'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON `sqlmesh__fhir`.*  TO '<user>'@'<deploy-ip>';
```

**`.env`** — `CONSOLIDATED_*` is the write-capable user:
```bash
CONSOLIDATED_HOST=<consolidé-host>
CONSOLIDATED_USER=<write-user>
CONSOLIDATED_PASS=<password>
FHIR_DB_NAME=fhir
PIPELINE_IMAGE=ghcr.io/mherman22/sedish-fhir-pipeline:main
```

**Deploy** — swap in the direct compose + set `swarm.sh` `SERVICE_NAMES` to `(fhir-pipeline)`:
```bash
cp docker-compose.direct.yml docker-compose.yml
./build-image.sh
./instant package init -n data-pipeline-consolidated-server --env-file .env
```

## Verify (either mode)
```bash
docker service logs -f data-pipeline-consolidated-server_fhir-pipeline
curl -su consolidated:consolidated \
  'http://openhim-core:5001/CR/fhir/Patient?identifier=http://sedish-haiti.org/fhir/source-key|<mspp>-<patient_id>'
```

## Routing — one deployment
The pipeline POSTs per-patient bundles to the `/consolidated/fhir` OpenHIM channel; the
`fhir-router-mediator` splits them: **Patient → OpenCR**, **clinical → SHR**. Set `CLINICAL_VIEWS=`
(empty) for identity-only (e.g. before the SHR is validated).
