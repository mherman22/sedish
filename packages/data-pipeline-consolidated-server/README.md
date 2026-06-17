# data-pipeline-consolidated-server

> Full end-to-end setup (both packages, configs, deploy, verify, troubleshooting):
> [`docs/consolidated-pipeline-setup.md`](../../docs/consolidated-pipeline-setup.md)

Consolidated-server → FHIR → OpenCR pipeline (SEDISH instant package). SQLMesh maps the CHARESS
**Consolidé** `consolidated_db` to FHIR R4 and a loader pushes Patient identities to **OpenCR**
(Phase 1; clinical to the **SHR** in Phase 2). The pipeline code/image is the separate repo
`github.com/mherman22/sedish-fhir-pipeline`; this package is the deploy wiring.

## Two modes

SQLMesh runs one SQL statement per model and MySQL can't JOIN across servers, so its **source and
output must be on the same server**. Which mode you use depends on the access CHARESS gives us:

| | **SYNC** (default) | **DIRECT** |
|---|---|---|
| When | Consolidé is **read-only** to us | We have **write** access to Consolidé |
| How | Sync `consolidated_db` into a local `pipeline-db`; SQLMesh runs there | SQLMesh runs on Consolidé itself (writes a `fhir` schema beside `consolidated_db`) |
| Local MySQL | yes (`pipeline-db` service) | none |
| Compose | `docker-compose.yml` (default) | `docker-compose.direct.yml` |
| Consolidé grant | `SELECT` on `consolidated_db` | `SELECT` on `consolidated_db` + write on `fhir`/`fhir_test` |

The **same image** does both — the entrypoint syncs only when `SRC_*` is set, otherwise it runs
directly against `FHIR_DB_*`.

## Common prerequisites
- OpenHIM is up with channels `/CR/fhir` (+ `/SHR/fhir` for Phase 2) and the `consolidated`
  client (role `emr`). The pipeline authenticates as one client for both channels.
- The deploy server's IP is allowed to reach the Consolidé MySQL.
- The pipeline image: pulled from GHCR (`PIPELINE_IMAGE=ghcr.io/mherman22/sedish-fhir-pipeline:main`,
  default) or built locally (`PIPELINE_IMAGE=sedish-fhir-pipeline:local` + `./build-custom-images.sh`).

---

## SYNC mode (default — read-only Consolidé)

**`.env`:**
```bash
# external read-only Consolidé — the source the pipeline syncs FROM (mapped to SRC_*)
CONSOLIDATED_HOST=<consolidated-host>
CONSOLIDATED_PORT=3306
CONSOLIDATED_USER=<read-only-user>
CONSOLIDATED_PASS=<password>
# local pipeline-db root password (optional; default 'pipeline')
PIPELINE_DB_PW=pipeline
# optional — sane defaults baked into the image:
#   FHIR_DB_NAME=fhir   OPENHIM_USER=consolidated   OPENHIM_PASS=consolidated
PIPELINE_IMAGE=ghcr.io/mherman22/sedish-fhir-pipeline:main
```
`FHIR_DB_*` and `SRC_*` are wired by the compose (you don't set them) — `pipeline-db` for the
local DB, `CONSOLIDATED_*` for the source.

**Deploy:**
```bash
./build-image.sh                                                  # after any package change
./instant package init -n data-pipeline-consolidated-server --env-file .env
```

**Sync cost.** The first sync is a full copy (one-time initial load); after that each cycle pulls
only `date_updated` deltas from the `*_openmrs` tables. Static reference tables (`concept`,
`concept_name`, dimensions — no `date_updated`) are synced **once** and skipped while populated, so
steady-state sync ≈ "the patients that changed," not a DB copy. `pipeline-db` does hold a full
working copy of `consolidated_db` (+ `fhir`), so size its disk accordingly. Set
`SYNC_REFRESH_STATIC=1` to force a re-copy of the reference tables (e.g. after a CIEL update).
*(DIRECT mode avoids the copy entirely.)*

---

## DIRECT mode (we have write access to Consolidé)

**`.env`** — `CONSOLIDATED_*` is now the write-capable user (it becomes `FHIR_DB_*`):
```bash
CONSOLIDATED_HOST=<consolidé host>
CONSOLIDATED_PORT=3306
CONSOLIDATED_USER=<user: SELECT on consolidated_db + write on fhir/fhir_test>
CONSOLIDATED_PASS=<password>
FHIR_DB_NAME=fhir
# OPENHIM_USER=consolidated   OPENHIM_PASS=consolidated   (optional)
PIPELINE_IMAGE=ghcr.io/mherman22/sedish-fhir-pipeline:main
```

**Grant on Consolidé (run as MySQL root there):**
```sql
CREATE DATABASE IF NOT EXISTS fhir;  CREATE DATABASE IF NOT EXISTS fhir_test;
GRANT SELECT ON consolidated_db.* TO '<user>'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON fhir.*      TO '<user>'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON fhir_test.* TO '<user>'@'<deploy-ip>';
FLUSH PRIVILEGES;
```

**Deploy** — swap in the direct compose (Swarm ignores compose `profiles`, so DIRECT is a
separate file), then build + init:
```bash
cp docker-compose.direct.yml docker-compose.yml      # use the no-pipeline-db, no-sync variant
./build-image.sh
./instant package init -n data-pipeline-consolidated-server --env-file .env
```
With no `SRC_*`, the entrypoint skips the sync and runs SQLMesh straight on Consolidé.

---

## Verify (either mode)
```bash
docker service logs -f data-pipeline-consolidated-server_fhir-pipeline
# a patient landed in the MPI (by the source-key identifier):
curl -su consolidated:consolidated \
  'http://openhim-core:5001/CR/fhir/Patient?identifier=http://sedish-haiti.org/fhir/source-key|<mspp>-<patient_id>'
```

## Routing — one deployment, no mode flag

Everything deploys **once** — OpenHIM, `shared-health-record-fhir`, `client-registry-opencr`, and
this package all come up together from `config.yaml` — and the pipeline then routes by resource
type, every cycle:
- **Patient** → OpenCR (`/CR/fhir`) — identity / matching, upserted on the source key.
- **clinical** (Encounter, Observation, Condition, Allergy, MedicationRequest) → SHR (`/SHR/fhir`),
  bundled per patient.
- **globals** (Location, …) → SHR.

Identity and clinical run off their own watermarks, so demographics flow to OpenCR and clinical to
the SHR at the same time. There is no `MPI_ONLY` switch and no second deployment — the resource
type *is* the routing decision.

**Identity-only (optional).** If you want to bring up identity first and hold clinical back (e.g. a
go-live before the SHR is validated), set `CLINICAL_VIEWS=` (empty) in the pipeline's environment —
clinical is then neither read nor pushed. It's a config value, not a redeploy of logic; clear it
later and the clinical backfills from where its watermark left off.
