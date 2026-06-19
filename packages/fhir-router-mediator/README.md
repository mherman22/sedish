# fhir-router-mediator (package)

> Full end-to-end setup (both packages, configs, deploy, verify, troubleshooting):
> [`docs/consolidated-pipeline-setup.md`](../../docs/consolidated-pipeline-setup.md)

Deploys the **fhir-router-mediator** into the SEDISH stack. Its source lives in its own repo,
[`github.com/mherman22/fhir-router-mediator`](https://github.com/mherman22/fhir-router-mediator),
whose CI publishes `ghcr.io/mherman22/fhir-router-mediator:main` — this package just pulls it.
It receives FHIR transaction Bundles from the data pipeline on the OpenHIM channel
`/consolidated/fhir` and routes them by resource type:

- **Patient → OpenCR** (`/CR/fhir`) — identity (PUT by uuid; OpenCR matches via `decisionRules`)
- **clinical → SHR** (`/SHR/fhir`) — Encounters, Observations, Conditions, Allergies, MedicationRequests

It de-dupes bundle entries by `resourceType/id` so a duplicate can't fail a whole transaction.

## How the channel is wired

The `/consolidated/fhir` channel (route → `fhir-router:3000`, `allow: [emr]`) is defined in the
**interoperability-layer-openhim** importer (`openhim-import.json`) — it is created when that
package's config-importer runs (i.e. on a clean deploy). This mediator does **not** self-register,
because this OpenHIM (v8 + Keycloak) does not expose a basic-auth root for `openhim-mediator-utils`.

## Image

Pulled from GHCR — `FHIR_ROUTER_IMAGE` defaults to `ghcr.io/mherman22/fhir-router-mediator:main`
(published by the repo's CI on push to `main`). Make that GHCR package public so the swarm can pull
it. To build locally instead (fallback), `./build-custom-images.sh` clones the repo and builds
`fhir-router-mediator:local`; set `FHIR_ROUTER_IMAGE=fhir-router-mediator:local`.

## Deploy

```bash
./build-image.sh                              # after any package change
./instant package init -n fhir-router-mediator --env-file .env
```

## Env

| Var | Default | Purpose |
|-----|---------|---------|
| `FHIR_ROUTER_IMAGE` | `ghcr.io/mherman22/fhir-router-mediator:main` | mediator image (from the repo's CI) |
| `OPENHIM_USER` / `OPENHIM_PASS` | `consolidated` / `consolidated` | OpenHIM client (role `emr`) for the CR/SHR channels |
| `CR_URL` / `SHR_URL` | `http://openhim-core:5001/CR/fhir` · `/SHR/fhir` | downstream channels |
