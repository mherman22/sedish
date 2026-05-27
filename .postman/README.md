# Postman Integration Tests

Integration tests for the SEDISH Haiti HIE, run via [Newman](https://github.com/postmanlabs/newman) (Postman CLI).

## Collections

| # | Collection | What it tests |
|---|-----------|---------------|
| 1 | OpenHIM Health | OpenHIM routing, auth enforcement, SHR + OpenCR reachability |
| 2 | Patient Registration (OpenCR) | Create, read, and search patients in the client registry |
| 3 | SHR FHIR Exchange | Patient, Observation, and Condition CRUD through SHR / HAPI FHIR |
| 4 | LNSP Lab Workflow | Lab orders, subscriptions via `/lnsp/` and `/dsub` channels |

## Prerequisites

- A running SEDISH HIE (via `instant project init` or Docker Swarm)
- [Node.js](https://nodejs.org/) (v18+)
- Newman: `npm install -g newman`

## Quick start

### Run all collections

```bash
.postman/run-tests.sh .postman/environments/local.postman_environment.json
```

### Run a single collection

```bash
newman run .postman/collections/1-openhim-health.postman_collection.json \
  --environment .postman/environments/local.postman_environment.json \
  --insecure
```

### Run with inline env vars (no file needed)

```bash
newman run .postman/collections/1-openhim-health.postman_collection.json \
  --env-var "baseUrl=https://openhimcore.sedishtest.live" \
  --env-var "clientId=openshr" \
  --env-var "clientPassword=openshr" \
  --insecure
```

## Environment setup

Edit `.postman/environments/local.postman_environment.json` before running:

| Variable | Description | Example |
|----------|-------------|---------|
| `baseUrl` | OpenHIM transaction endpoint | `https://openhimcore.sedishtest.live` |
| `clientId` | OpenHIM client ID | `openshr` or `postman-test` |
| `clientPassword` | OpenHIM client password | `openshr` or `postman-test` |

> **Note:** The `postman-test` client (added in `openhim-import.json`) has access to all channels including LNSP/XDS. If it hasn't been imported yet, use `openshr`/`openshr` for collections 1-3.

## Using Postman GUI

1. Open Postman
2. **Import** the collections from `.postman/collections/`
3. **Import** an environment from `.postman/environments/`
4. Update the environment variables to match your setup
5. Run collections from the Collection Runner

## Test results

When run via `run-tests.sh`, JUnit XML results are saved to `.postman/results/` (git-ignored).
