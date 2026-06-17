# Consolidated Pipeline — Setup Guide

End-to-end guide for the two packages that bring the external **Consolidé** server into SEDISH:

| Package | Service(s) | Role |
|---------|-----------|------|
| `data-pipeline-consolidated-server` | `pipeline-db`, `fhir-pipeline` | Sync Consolidé → map to FHIR (SQLMesh) → POST per-patient bundles to OpenHIM |
| `fhir-router-mediator` | `fhir-router` | OpenHIM mediator: split each bundle → `Patient`→OpenCR, clinical→SHR |

## 1. Architecture / data flow

```
  Consolidé consolidated_db            data-pipeline-consolidated-server                    OpenHIM
  (EXTERNAL, read-only MySQL) ──sync──▶ pipeline-db (local MySQL copy)                      channels
                                          │  SQLMesh: consolidated_db → fhir.*
                                          │  loader: POST transaction Bundles
                                          ▼
                              POST /consolidated/fhir ──▶ fhir-router ──PUT /CR/fhir──▶ OpenCR  (identity)
                                                          (split+dedupe) ──POST /SHR/fhir─▶ SHR (clinical)
```

- The pipeline does **not** know about CR/SHR — it POSTs one bundle (Patient + its changed clinical)
  per patient to a single channel, `/consolidated/fhir`.
- The mediator routes by resource type: `Patient` → `PUT /CR/fhir/Patient/{id}` (OpenCR has no
  conditional-update, so PUT by uuid; OpenCR matches via `decisionRules.json`), clinical → one
  `POST /SHR/fhir` transaction Bundle. It de-dupes entries by `resourceType/id`.
- Everything is incremental (per-resource watermarks) and idempotent — re-runs converge.

**Repos (images):**
- pipeline → `github.com/mherman22/sedish-fhir-pipeline` → `ghcr.io/mherman22/sedish-fhir-pipeline:main`
- mediator → `github.com/mherman22/fhir-router-mediator` → `ghcr.io/mherman22/fhir-router-mediator:main`

## 2. Prerequisites

- The core SEDISH stack deploys these first (already in `config.yaml`, in order):
  `interoperability-layer-openhim`, `shared-health-record-fhir`, `client-registry-opencr`, then
  `data-pipeline-consolidated-server`, then `fhir-router-mediator`.
- Read-only MySQL access to Consolidé (host/user/pass).
- Both GHCR images **pullable** — either make the GHCR packages public, or build locally (§4).

## 3. `.env` configuration

The two packages read these from `.env` (defaults come from each package's `package-metadata.json`,
so you only set what differs). What's there now:

```bash
# --- data-pipeline-consolidated-server (Consolidé → OpenCR + SHR) ---
# Required — the external Consolidé MySQL we sync from:
CONSOLIDATED_HOST=<consolidated-host>
CONSOLIDATED_PORT=3306
CONSOLIDATED_USER=<read-only-user>
CONSOLIDATED_PASS=<password>
# Image to deploy (set to sedish-fhir-pipeline:local for a locally-built image):
PIPELINE_IMAGE=ghcr.io/mherman22/sedish-fhir-pipeline:main
```

Full set of knobs (defaults shown — only override in `.env` if needed):

| Variable | Default | Package | Purpose |
|----------|---------|---------|---------|
| `CONSOLIDATED_HOST/PORT/USER/PASS` | — (host etc. required) | pipeline | external Consolidé MySQL (read-only); wired to the loader's `SRC_*` |
| `PIPELINE_IMAGE` | `ghcr.io/mherman22/sedish-fhir-pipeline:main` | pipeline | pipeline image (`sedish-fhir-pipeline:local` to build locally) |
| `PIPELINE_DB_PW` | `pipeline` | pipeline | local `pipeline-db` root password |
| `FHIR_ROUTER_IMAGE` | `ghcr.io/mherman22/fhir-router-mediator:main` | mediator | mediator image (`fhir-router-mediator:local` to build locally) |
| `OPENHIM_USER` / `OPENHIM_PASS` | `consolidated` / `consolidated` | both | the one OpenHIM client (role `emr`) used for the channels |

Knobs handled by the image defaults (override via the compose env only if you must):
`MEDIATOR_URL` (`http://openhim-core:5001/consolidated/fhir`), `BATCH_SIZE` (`100`),
`CLINICAL_VIEWS` (set **empty** for identity-only — Patient→OpenCR, no clinical→SHR),
`CR_URL`/`SHR_URL`, `SYNC_REFRESH_STATIC` (force a re-copy of reference tables).

## 4. Make the images pullable

**Option A — GHCR (default).** Make both packages public once (GitHub → the package → visibility →
Public). Then the default `:main` images pull anonymously.

**Option B — build locally.** No GHCR access needed:
```bash
./build-custom-images.sh        # builds sedish-fhir-pipeline:local AND fhir-router-mediator:local
# then in .env:
PIPELINE_IMAGE=sedish-fhir-pipeline:local
FHIR_ROUTER_IMAGE=fhir-router-mediator:local
```

## 5. Deploy

> **Rule:** any change under `packages/` only reaches `./instant` after `./build-image.sh` rebuilds
> the management image — otherwise you get "Unknown package id".

```bash
git pull fork hie
./build-image.sh
```

**Channel creation (important).** The `/consolidated/fhir` channel is created by the OpenHIM
**config-importer**, which only runs successfully on a **fresh** OpenHIM Mongo (on an already-used
Mongo it can't re-authenticate — root is Keycloak-managed — and sits at `0/1`). So for the channel
to exist either deploy onto clean Mongo, or wipe just the OpenHIM Mongo volume:

```bash
docker stack rm openhim
docker volume rm openhim_openhim-mongo-01 openhim_openhim-mongo-01-config   # only the OpenHIM mongo;
                                                                            # OpenCR/SHR data is elsewhere
```

Then bring the packages up (OpenHIM first so the channel + `consolidated` client exist):

```bash
./instant package init -n interoperability-layer-openhim    --env-file .env   # importer → all channels
./instant package init -n data-pipeline-consolidated-server --env-file .env
./instant package init -n fhir-router-mediator              --env-file .env
```

To redeploy a single package later, use `up` (and `--force` the image to re-pull `:main`):
```bash
./instant package up -n fhir-router-mediator --env-file .env
docker service update --force --image ghcr.io/mherman22/fhir-router-mediator:main fhir-router-mediator_fhir-router
```

## 6. Verify

```bash
# services up
docker service ls | grep -E 'fhir-pipeline|pipeline-db|fhir-router'
#   data-pipeline-consolidated-server_pipeline-db    1/1
#   data-pipeline-consolidated-server_fhir-pipeline  1/1
#   fhir-router-mediator_fhir-router                 1/1

# the channel exists (expect 1)
docker exec $(docker ps -qf name=openhim_mongo) mongo openhim --quiet \
  --eval 'db.channels.count({urlPattern:/consolidated/})'

# pipeline is pushing cleanly (expect "-> 200/201" and "cycle done — clean")
docker service logs -f data-pipeline-consolidated-server_fhir-pipeline

# mediator is routing (expect "bundle routed" with failures:0)
docker service logs -f fhir-router-mediator_fhir-router

# data landed: OpenCR populated + SHR has clinical
docker exec $(docker ps -qf name=_opencr.) sh -c 'wget -qO- "http://localhost:3000/fhir/Patient?_summary=count"'
```

## 7. Operating notes

- **Sync cost.** First sync is a full copy; afterwards only `date_updated` deltas. Static reference
  tables (`concept`, `concept_name`, dimensions) sync **once** and are skipped while populated
  (`SYNC_REFRESH_STATIC=1` forces a re-copy). `pipeline-db` holds a full working copy of
  `consolidated_db` + the `fhir` output — size its disk accordingly.
- **Identity-only.** Set `CLINICAL_VIEWS=` (empty) to send only Patient→OpenCR (e.g. before the SHR
  is validated). Clear it later and clinical backfills from its watermark.
- **DIRECT mode.** If Consolidé ever grants write access, the pipeline can run SQLMesh directly on
  Consolidé (no local copy) — see `packages/data-pipeline-consolidated-server/README.md`.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `config-importer` at `0/1`, `/consolidated/fhir` missing | importer can't re-auth on a used Mongo (Keycloak) | deploy on clean Mongo / wipe the OpenHIM mongo volume (§5) |
| Loader `CR=ERR 404 Cannot PUT /fhir/Patient` | running the **old** loader (conditional PUT) | deploy the current pipeline image; the mediator PUTs by id |
| SHR `HAPI-0535 ... multiple resources with the same id` | duplicate clinical rows | fixed: `concept_name` fan-out fix in the models + mediator de-dupe |
| Loader `ERR 500` on a big identity page, watermark never advances | bundle outlasts OpenHIM's timeout (sequential CR PUTs) | `BATCH_SIZE=100` (default); raise channel timeout if you need bigger |
| `fhir-router` won't start / image pull `unauthorized` | GHCR package private | make it public, or `FHIR_ROUTER_IMAGE=fhir-router-mediator:local` (§4) |
| "Unknown package id" | management image not rebuilt | `./build-image.sh` before `./instant` |
