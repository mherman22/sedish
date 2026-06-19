# fhir-router-mediator (package)

> Full end-to-end setup (both packages, configs, deploy, verify, troubleshooting):
> [`docs/consolidated-pipeline-setup.md`](../../docs/consolidated-pipeline-setup.md)

Deploys the **fhir-router-mediator** into the SEDISH stack. Its source is vendored in this repo at
[`projects/fhir-router-mediator`](../../projects/fhir-router-mediator) and built locally by
`./build-custom-images.sh` (it is no longer a separate repo / GHCR pull). It receives FHIR
transaction Bundles from the data pipeline on the OpenHIM channel `/consolidated/fhir` and routes
them by resource type:

- **Patient → OpenCR** (`/CR/fhir`) — identity (PUT by uuid; OpenCR matches via `decisionRules`)
- **clinical → SHR** (`/SHR/fhir`) — Encounters, Observations, Conditions, Allergies, MedicationRequests

It de-dupes bundle entries by `resourceType/id` so a duplicate can't fail a whole transaction.

## How the channel is wired

The `/consolidated/fhir` channel (route → `fhir-router:3000`, `allow: [emr]`) is defined in the
**interoperability-layer-openhim** importer (`openhim-import.json`) — it is created when that
package's config-importer runs (i.e. on a clean deploy). This mediator does **not** self-register,
because this OpenHIM (v8 + Keycloak) does not expose a basic-auth root for `openhim-mediator-utils`.

## Image

Built from the in-repo source — `FHIR_ROUTER_IMAGE` defaults to `fhir-router-mediator:local`:

```bash
./build-custom-images.sh                      # builds fhir-router-mediator:local from projects/
```

Override `FHIR_ROUTER_IMAGE` to pull a prebuilt image instead. Source + unit tests live in
[`projects/fhir-router-mediator`](../../projects/fhir-router-mediator) (`npm test`).

## Deploy

```bash
./build-image.sh                              # after any package change
./instant package init -n fhir-router-mediator --env-file .env
```

## Env

| Var | Default | Purpose |
|-----|---------|---------|
| `FHIR_ROUTER_IMAGE` | `fhir-router-mediator:local` | mediator image (built by `./build-custom-images.sh`) |
| `OPENHIM_USER` / `OPENHIM_PASS` | `consolidated` / `consolidated` | OpenHIM client (role `emr`) for the CR/SHR channels |
| `CR_URL` / `SHR_URL` | `http://openhim-core:5001/CR/fhir` · `/SHR/fhir` | downstream channels |
