# Sedish HIE — Setup & Redeployment Guide

## Table of Contents

- [Fresh Server Setup](#fresh-server-setup)
- [Redeploying After Changes](#redeploying-after-changes)
- [Full Teardown & Redeploy](#full-teardown--redeploy)
- [Key Services & URLs](#key-services--urls)
- [Package Names Reference](#package-names-reference)
- [Debugging Checklist](#debugging-checklist)
- [OpenHIM Client Password Hash Generation](#openhim-client-password-hash-generation)

---

## Fresh Server Setup

### 1. Install prerequisites

```bash
sudo apt-get update && sudo apt-get install -y git jq

# Git LFS — CRITICAL: without this, .omod and .sql files will be 132-byte pointer stubs
curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | sudo bash
sudo apt-get install -y git-lfs
git lfs install
```

Install Docker CE by following the [official Docker CE guide](https://docs.docker.com/engine/install/ubuntu/).

### 2. Initialize Docker Swarm

```bash
docker swarm init
```

### 3. Clone and pull LFS files

```bash
git clone https://github.com/charess-org/sedish.git
cd sedish
git lfs pull
```

Verify `.omod` files are real (should be megabytes, **not** 132 bytes):

```bash
ls -lh packages/emr-isanteplus/config/custom_modules/
```

### 4. Configure environment

```bash
cp .env.hie .env
```

Edit `.env` and set at minimum:

- `DOMAIN_NAME` — your domain (e.g. `sedishtest.live`)
- `SUBDOMAINS` — comma-separated list of all subdomains
- `RENEWAL_EMAIL` — email for Let's Encrypt certificate notifications
- `STAGING` — set to `false` for production certificates

### 5. Get the Instant OpenHIE CLI

```bash
./get-cli.sh linux
```

### 6. Create required host directories

```bash
sudo mkdir -p /backups/elasticsearch
sudo mkdir -p /tmp/backups
```

### 7. Build custom Docker images

```bash
./build-custom-images.sh   # builds isanteplus-mysql, analytics elasticsearch, and other custom images
./build-image.sh           # builds the management/deployment image
```

### 8. Deploy everything (first time only)

```bash
./instant project init --env-file .env
```

> **Use `init` only once per clean environment.** This provisions SSL certificates, initializes databases, imports OpenHIM channels/clients, and starts all services.

### 9. Apply HAPI FHIR configuration overrides

```bash
./packages/fhir-datastore-hapi-fhir/post-deploy.sh
```

> **Run this after every `package up` or `project init` that includes `fhir-datastore-hapi-fhir`.** The instant tooling does not apply env vars with dots (e.g. `hapi.fhir.*`) from `docker-compose.yml`. This script sets `enforce_referential_integrity_on_write=false` and `auto_create_placeholder_reference_targets=true`, which are required for the data pipeline to push resources without strict reference ordering.

### 10. Verify deployment

```bash
docker service ls
```

All services should show their expected replica count (e.g. `1/1`). Check for any services stuck at `0/1`:

```bash
docker service ls --format '{{.Name}} {{.Replicas}}' | grep '0/'
```

> **Note:** `await-helper` services at `0/1` are normal — they run once during init and exit.

### 11. Wait for iSantePlus to boot

iSantePlus/OpenMRS instances take **5–10 minutes** to fully start (module loading, liquibase migrations, Spring context refresh). Wait until the login page loads before proceeding.

### 12. Trigger initial pipeline run

Once iSantePlus is fully booted:

```bash
curl -X POST 'https://pipeline.<your-domain>/run?runMode=FULL'
```

Or use the Pipeline Control Panel UI at `https://pipeline.<your-domain>`.

> The first full run creates the DWH baseline. After that, incremental runs fire automatically every hour.

---

## Redeploying After Changes

### Redeploy a single package

```bash
./instant package down -n <package-name> --env-file .env
./instant package up -n <package-name> --env-file .env
```

### If the package is `fhir-datastore-hapi-fhir`

Also run the post-deploy script:

```bash
./packages/fhir-datastore-hapi-fhir/post-deploy.sh
```

### If OpenHIM MongoDB was wiped

Use `init` (not `up`) so the replica set and config importer run:

```bash
./instant package init -n interoperability-layer-openhim --env-file .env
```

### If the pipeline config (`application.yaml`) was changed

The pipeline's `application.yaml` is injected as a Docker config. Docker configs are immutable, so you must create a new one and swap it:

```bash
# Check current config name
docker service inspect pipeline_streaming-pipeline \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Configs}}{{.ConfigName}}{{println}}{{end}}'

# Create new config and swap
docker config create pipeline_application_yaml_vN packages/data-pipeline-isanteplus/config/application.yaml
docker service update \
  --config-rm <old_config_name> \
  --config-add source=pipeline_application_yaml_vN,target=/app/config/application.yaml \
  pipeline_streaming-pipeline
```

---

## Full Teardown & Redeploy

```bash
# 1. Tear down everything
./instant project down --env-file .env

# 2. Redeploy
./instant project init --env-file .env

# 3. Apply HAPI FHIR overrides
./packages/fhir-datastore-hapi-fhir/post-deploy.sh

# 4. Verify all services are up
docker service ls

# 5. Wait 5-10 minutes for iSantePlus to boot, then trigger pipeline
curl -X POST 'https://pipeline.<your-domain>/run?runMode=FULL'
```

---

## Key Services & URLs

| Service | URL | Notes |
|---|---|---|
| iSantePlus (HUEH) | `https://hueh.<domain>/openmrs` | Takes 5–10 min to boot |
| iSantePlus (La Paix) | `https://lapaix.<domain>/openmrs` | |
| iSantePlus (OFATMA) | `https://ofatma.<domain>/openmrs` | |
| iSantePlus (Foyer St-Camille) | `https://foyer-saint-camille.<domain>/openmrs` | |
| OpenHIM Console | `https://openhimconsole.<domain>` | Login: `root@openhim.org` / `instant101` |
| OpenCR | `https://opencr.<domain>/crux` | |
| Grafana | `https://grafana.<domain>` | |
| Keycloak | `https://keycloak.<domain>` | |
| Pipeline Control Panel | `https://pipeline.<domain>` | Run Full/Incremental from UI |

---

## Package Names Reference

| Package ID | Description |
|---|---|
| `reverse-proxy-nginx` | Nginx reverse proxy + Let's Encrypt SSL |
| `interoperability-layer-openhim` | OpenHIM Core + Console + MongoDB |
| `fhir-datastore-hapi-fhir` | HAPI FHIR R4 Server |
| `shared-health-record-fhir` | SHR Mediator (proxies FHIR to HAPI) |
| `data-pipeline-isanteplus` | FHIR Data Pipes (micro-batching pipeline) |
| `emr-isanteplus` | iSantePlus (OpenMRS) instances |
| `client-registry-opencr` | OpenCR Client Registry |
| `database-postgres` | PostgreSQL (HAPI FHIR, Keycloak) |
| `database-mysql` | MySQL (iSantePlus) |
| `identity-access-manager-keycloak` | Keycloak SSO |
| `monitoring` | Grafana + Prometheus + Loki |

---

## Debugging Checklist

### Which services are down?

```bash
docker service ls --format '{{.Name}} {{.Replicas}}' | grep '0/'
```

### Check logs for a failing service

```bash
docker service logs <service_name> --tail 50
```

### Check SSL certificate status

```bash
docker exec $(docker ps -q -f name=reverse-proxy_reverse-proxy-nginx) \
  openssl x509 -in /run/secrets/fullchain.pem -noout -issuer -dates
```

- If issuer contains `(STAGING)` → dummy cert, need to redeploy nginx for real cert.

### Fix OpenHIM MongoDB "NotWritablePrimary"

```bash
# Check replica set status
docker exec $(docker ps -q -f name=openhim_mongo-1) mongo --eval "rs.status()"

# If "NotYetInitialized", initialize it:
docker exec $(docker ps -q -f name=openhim_mongo-1) mongo --eval \
  'rs.initiate({_id:"mongo-set",members:[{_id:0,host:"mongo-1:27017"}]})'

# Then re-run init to import channels/clients:
./instant package init -n interoperability-layer-openhim --env-file .env
```

### Fix HAPI FHIR "database does not exist"

```bash
docker exec $(docker ps -q -f name=postgres_postgres-1) \
  env PGPASSWORD=instant101 psql -U postgres -c 'CREATE DATABASE hapi;'
docker service update --force hapi-fhir_hapi-fhir
```

### Check HAPI FHIR referential integrity setting

```bash
docker service inspect hapi-fhir_hapi-fhir \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}' | grep referential
```

If missing, run `./packages/fhir-datastore-hapi-fhir/post-deploy.sh`.

### Check pipeline sink config

```bash
docker exec $(docker ps -q -f name=pipeline_streaming-pipeline) \
  cat /app/config/application.yaml | grep -A2 sink
```

Should show `sinkUserName: "shr-pipeline"` and `sinkPassword: "instant101"`.

### Test pipeline auth to OpenHIM

```bash
docker exec $(docker ps -q -f name=pipeline_streaming-pipeline) \
  curl -s -u 'shr-pipeline:instant101' http://openhim-core:5001/SHR/fhir/metadata | head -c 100
```

Should return `{"resourceType":"CapabilityStatement"...}`.

### Force restart a stuck service

```bash
docker service update --force <service_name>
```

---

## OpenHIM Client Password Hash Generation

When you need to set or change an OpenHIM client password (e.g. `shr-pipeline`):

```python
python3 -c "
import hashlib, os
password = 'instant101'           # change as needed
salt = os.urandom(16).hex()
hash_val = hashlib.sha512((password + salt).encode()).hexdigest()
print(f'passwordSalt: {salt}')
print(f'passwordHash: {hash_val}')
"
```

> **Formula**: `sha512(password + salt)` — **password first, then salt**.

Update the hash in both:
1. `packages/interoperability-layer-openhim/importer/volume/openhim-import.json` — the client entry
2. `packages/data-pipeline-isanteplus/config/application.yaml` — `sinkPassword` must match
