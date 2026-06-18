# data-pipeline-consolidated-server

> Full end-to-end setup (both packages, configs, deploy, verify, troubleshooting):
> [`docs/consolidated-pipeline-setup.md`](../../docs/consolidated-pipeline-setup.md)

Maps the CHARESS **Consolidé** `consolidated_db` to FHIR R4 with SQLMesh and routes it through
OpenHIM — Patient identities to **OpenCR**, clinical to the **SHR** (via the `fhir-router-mediator`).
The pipeline code/image is the separate repo `github.com/mherman22/sedish-fhir-pipeline`; this
package is the deploy wiring.

## How it runs (DIRECT — the only mode)

SQLMesh runs **on the Consolidé server itself**: it reads `consolidated_db` and writes the FHIR
output into dedicated schemas on that same server. There is **no local database and no copy** of
`consolidated_db` — copying a constantly-changing production DB doesn't make sense, so we transform
in place.

## Prerequisite (CHARESS, one-time)

CHARESS must **pre-create three schemas** on the Consolidé MySQL and grant one user on them. No
global `CREATE` is needed (the image runs with `ENSURE_DBS=0`); no `fhir_test` (no tests in prod);
`ref` is folded into `fhir` and the loader's state table lives inside `fhir`.

```sql
CREATE DATABASE fhir;  CREATE DATABASE sqlmesh;  CREATE DATABASE sqlmesh__fhir;

-- one user reads the source AND writes the output (single connection, cross-schema):
CREATE USER 'sedish_fhir'@'<deploy-ip>' IDENTIFIED BY '<password>';
GRANT SELECT         ON consolidated_db.*  TO 'sedish_fhir'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON fhir.*             TO 'sedish_fhir'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON sqlmesh.*          TO 'sedish_fhir'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON `sqlmesh__fhir`.*  TO 'sedish_fhir'@'<deploy-ip>';
FLUSH PRIVILEGES;
```

Also required: OpenHIM up with the `/consolidated/fhir` channel (the `fhir-router-mediator` package)
and the `consolidated` client (role `emr`); and the deploy server's IP allowed to reach the
Consolidé MySQL.

## `.env`

`CONSOLIDATED_*` is the write-capable user above (it becomes `FHIR_DB_*`):
```bash
CONSOLIDATED_HOST=<consolidé-host>
CONSOLIDATED_PORT=3306
CONSOLIDATED_USER=sedish_fhir
CONSOLIDATED_PASS=<password>
FHIR_DB_NAME=fhir
# OPENHIM_USER=consolidated   OPENHIM_PASS=consolidated   (optional, defaults shown)
PIPELINE_IMAGE=ghcr.io/mherman22/sedish-fhir-pipeline:main
```
`FHIR_DB_*`, `FHIR_TEST_DB=`, `ENSURE_DBS=0`, `STATE_DB=fhir` are wired by the compose — you don't
set them. There is no `SRC_*` (its absence is what makes SQLMesh run in place) and no `PIPELINE_DB_PW`
(no local database).

## Deploy

```bash
./build-image.sh                                                  # after any package change
./instant package init -n data-pipeline-consolidated-server --env-file .env
```

## Verify

```bash
docker service logs -f data-pipeline-consolidated-server_fhir-pipeline
# a patient landed in the MPI (by the source-key identifier):
curl -su consolidated:consolidated \
  'http://openhim-core:5001/CR/fhir/Patient?identifier=http://sedish-haiti.org/fhir/source-key|<mspp>-<patient_id>'
```

## Routing — one deployment

Everything deploys **once** — OpenHIM, `shared-health-record-fhir`, `client-registry-opencr`,
`fhir-router-mediator`, and this package all come up from `config.yaml`. The pipeline POSTs per-patient
FHIR bundles to `/consolidated/fhir`; the mediator routes by resource type, every cycle:
- **Patient** → OpenCR (`/CR/fhir`) — identity / matching.
- **clinical** (Encounter, Observation, Condition, Allergy, MedicationRequest) → SHR (`/SHR/fhir`).

**Identity-only (optional).** Set `CLINICAL_VIEWS=` (empty) to send only Patient→OpenCR (e.g. a
go-live before the SHR is validated). Clear it later and clinical backfills from its watermark.
