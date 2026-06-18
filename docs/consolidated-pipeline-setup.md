# Consolidated Pipeline — Setup Guide

End-to-end guide for the two packages that bring the CHARESS **Consolidé** server into SEDISH:

| Package | Service | Role |
|---------|---------|------|
| `data-pipeline-consolidated-server` | `fhir-pipeline` | Map `consolidated_db` → FHIR (SQLMesh, in place on Consolidé) → POST per-patient bundles to OpenHIM |
| `fhir-router-mediator` | `fhir-router` | OpenHIM mediator: split each bundle → `Patient`→OpenCR, clinical→SHR |

## 1. Architecture / data flow

```
  Consolidé MySQL (CHARESS)                                         OpenHIM
  ├─ consolidated_db        ── SQLMesh reads ──┐
  └─ fhir / sqlmesh /                          │  maps in place (NO copy)
     sqlmesh__fhir          ◀─ SQLMesh writes ─┘
                                          │  loader POSTs per-patient bundles
                                          ▼
                              POST /consolidated/fhir ──▶ fhir-router ──PUT /CR/fhir──▶ OpenCR (identity)
                                                          (split+dedupe) ──POST /SHR/fhir─▶ SHR (clinical)
```

**DIRECT mode is the only mode.** SQLMesh runs **on the Consolidé server**, reading `consolidated_db`
and writing the FHIR output into schemas on that same server — there is **no local database and no
copy** of `consolidated_db`. (Copying a constantly-changing production DB doesn't make sense; the
pipeline transforms in place.)

**Repos (images):**
- pipeline → `github.com/mherman22/sedish-fhir-pipeline` → `ghcr.io/mherman22/sedish-fhir-pipeline:main`
- mediator → `github.com/mherman22/fhir-router-mediator` → `ghcr.io/mherman22/fhir-router-mediator:main`

## 2. Prerequisite — CHARESS creates 3 schemas + grants one user (one-time)

This is the hard dependency: without write access to Consolidé and these schemas, the pipeline
cannot run. Ask CHARESS to run, as MySQL root (replace `<password>` / `<deploy-ip>`):

```sql
CREATE DATABASE fhir;  CREATE DATABASE sqlmesh;  CREATE DATABASE sqlmesh__fhir;

CREATE USER 'sedish_fhir'@'<deploy-ip>' IDENTIFIED BY '<password>';
GRANT SELECT         ON consolidated_db.*  TO 'sedish_fhir'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON fhir.*             TO 'sedish_fhir'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON sqlmesh.*          TO 'sedish_fhir'@'<deploy-ip>';
GRANT ALL PRIVILEGES ON `sqlmesh__fhir`.*  TO 'sedish_fhir'@'<deploy-ip>';
FLUSH PRIVILEGES;
```

- No global `CREATE`/`SUPER`; read-only on `consolidated_db`, full only on the 3 schemas.
- **One user**, by design — the transform reads `consolidated_db` and writes `fhir` in the same
  statement over one connection, so the connecting account needs both.
- Confirm `<deploy-ip>` can reach the Consolidé MySQL port.

The core SEDISH stack also deploys (already in `config.yaml`, in order):
`interoperability-layer-openhim`, `shared-health-record-fhir`, `client-registry-opencr`, then
`data-pipeline-consolidated-server`, then `fhir-router-mediator`.

## 3. `.env`

```bash
# pipeline (DIRECT) — CONSOLIDATED_* is the write-capable user above
CONSOLIDATED_HOST=<consolidé-host>
CONSOLIDATED_PORT=3306
CONSOLIDATED_USER=sedish_fhir
CONSOLIDATED_PASS=<password>
FHIR_DB_NAME=fhir
PIPELINE_IMAGE=ghcr.io/mherman22/sedish-fhir-pipeline:main

# mediator
FHIR_ROUTER_IMAGE=ghcr.io/mherman22/fhir-router-mediator:main
# OPENHIM_USER=consolidated  OPENHIM_PASS=consolidated   (optional, defaults shown)
```
`FHIR_DB_*`, `FHIR_TEST_DB=`, `ENSURE_DBS=0`, `STATE_DB=fhir` are wired by the compose (don't set
them). No `SRC_*`, no `PIPELINE_DB_PW` — there's no local database.

## 4. Make the images pullable

GHCR (default): make both packages public once (GitHub → package → visibility → Public). Or build
locally — `./build-custom-images.sh` builds `sedish-fhir-pipeline:local` + `fhir-router-mediator:local`;
then set `PIPELINE_IMAGE`/`FHIR_ROUTER_IMAGE` to the `:local` tags.

## 5. Deploy

```bash
git pull fork hie
./build-image.sh                 # required before ./instant after any packages/ change

# OpenHIM channel /consolidated/fhir is created by the config-importer on a CLEAN Mongo
# (it can't re-import on a used Mongo). On a fresh stack it's automatic; otherwise wipe just
# the OpenHIM mongo volume so the importer runs:
#   docker stack rm openhim
#   docker volume rm openhim_openhim-mongo-01 openhim_openhim-mongo-01-config

./instant package init -n interoperability-layer-openhim    --env-file .env
./instant package init -n fhir-router-mediator              --env-file .env
./instant package init -n data-pipeline-consolidated-server --env-file .env
```

## 6. Verify

```bash
docker service ls | grep -E 'fhir-pipeline|fhir-router'
#   data-pipeline-consolidated-server_fhir-pipeline  1/1   (no pipeline-db — DIRECT)
#   fhir-router-mediator_fhir-router                 1/1

docker exec $(docker ps -qf name=openhim_mongo) mongo openhim --quiet \
  --eval 'db.channels.count({urlPattern:/consolidated/})'    # expect 1

docker service logs -f data-pipeline-consolidated-server_fhir-pipeline   # expect cycles, no sync/copy
docker exec $(docker ps -qf name=_opencr.) sh -c 'wget -qO- "http://localhost:3000/fhir/Patient?_summary=count"'
```

## 7. Operating notes

- **Change detection.** Incremental on `GREATEST(date_updated, date_changed, date_created)` — new
  patients are caught via `date_created` (set on INSERT). Edits surface via `date_changed` *if*
  Consolidé populates it; a periodic reconcile (`SYNC_RECONCILE_EVERY`, default 1h) catches
  edits/deletes the timestamps miss.
- **Identity-only.** `CLINICAL_VIEWS=` (empty) sends only Patient→OpenCR (e.g. before the SHR is
  validated); clear it later and clinical backfills from its watermark.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| pipeline can't connect / "Access denied" | schemas/grants not in place | run the §2 SQL on Consolidé |
| pipeline errors creating schema | image tried to CREATE | ensure `ENSURE_DBS=0` (it's set in the compose) |
| `/consolidated/fhir` 404 / importer `0/1` | channel not imported (used Mongo) | deploy on clean Mongo / wipe the OpenHIM mongo volume (§5) |
| `fhir-router` won't start / image pull `unauthorized` | GHCR package private | make it public, or use `:local` (§4) |
| "Unknown package id" | management image not rebuilt | `./build-image.sh` before `./instant` |
