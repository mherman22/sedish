# SEDISH: Haiti Health Information Exchange

A Docker Swarm-based Health Information Exchange (HIE) for Haiti, built on [Instant OpenHIE v2](https://jembi.gitbook.io/instant-v2). SEDISH connects multiple iSantePlus (OpenMRS) clinic sites to a centralized data exchange layer for patient identity management, shared health records, and clinical data analytics.

---

## Architecture

```
                                 ┌──────────────┐
                                 │   OpenCR     │
                                 │ (Patient MPI)│
                                 └──────▲───────┘
                                        │ /CR/fhir
┌─────────────┐                 ┌───────┴────────┐                ┌──────────────┐
│ iSantePlus  │──mpi-client───▶│                 │                │              │
│ (Site 1)    │                 │    OpenHIM      │───/SHR/fhir──▶│  HAPI FHIR   │
│ iSantePlus  │──mpi-client───▶│  (Mediator)     │                │   (SHR)      │
│ (Site 2)    │                 │                 │                │              │
│ iSantePlus  │──mpi-client───▶│   Port 5001     │                └──────▲───────┘
│ (Site 3)    │                 └────────▲────────┘                       │
└─────────────┘                          │                                │
       │                          ┌──────┴───────┐               ┌───────┴───────┐
       │                          │  SHR Mediator │               │  FHIR Data    │
       └──────────────────────────│  (Express.js) │──────────────▶│  Pipeline     │
              xds-sender          └──────────────┘  (batch sync)  │  (per site)   │
           (lab orders only)                                      └───────────────┘
```

**Data flows:**

| Flow | Trigger | Path | Purpose |
|------|---------|------|---------|
| Patient identity | Patient create/update | iSantePlus → OpenHIM → OpenCR | Real-time MPI registration via `mpi-client` module |
| Clinical documents | Lab order (VL/EID) | iSantePlus → OpenHIM → SHR → HAPI FHIR | Real-time via `xds-sender` module |
| Batch data sync | Every 5 minutes | Pipeline → iSantePlus (FHIR API) → OpenHIM → SHR → HAPI FHIR | Incremental sync of all resource types |

---

## Components

| Component | Image | Purpose |
|-----------|-------|---------|
| [iSantePlus](https://github.com/IsantePlus/openmrs-distro-isanteplus) | `itechuw/docker-isanteplus-server:local-2` | OpenMRS-based EMR (multiple clinic instances) |
| [OpenHIM](http://openhim.org/) | `jembi/openhim-core:v8.5.0` | Interoperability layer — routes, logs, and secures all data exchange |
| [OpenCR](https://github.com/intrahealth/client-registry) | `itechuw/opencr` | Master Patient Index (MPI) — de-duplicates patient identities |
| [HAPI FHIR](https://hapifhir.io/) | `jembi/hapi:v7.0.3-wget` | FHIR R4 data store — Shared Health Record (SHR) |
| [SHR Mediator](https://github.com/DIGI-UW/shared-health-record) | `itechuw/shared-health-record:main` | Proxies FHIR requests to HAPI FHIR with validation |
| [FHIR Data Pipeline](https://github.com/google/fhir-data-pipes) | `us-docker.pkg.dev/.../fhir-analytics/main:latest` | Batch/incremental sync from iSantePlus to SHR |
| [Keycloak](https://www.keycloak.org/) | `keycloak/keycloak:20.0` | Identity and access management (SSO) |
| [Nginx](https://nginx.org/) | `nginx:stable` | Reverse proxy with Let's Encrypt SSL |
| Monitoring | Grafana + Prometheus + Loki | Dashboards, metrics, and log aggregation |

---

## Prerequisites

- **Server**: Ubuntu 20.04+ with at least 16 GB RAM (32 GB recommended)
- **Docker**: Docker CE with Swarm mode
- **Domain**: A domain with wildcard DNS (`*.yourdomain.com`) pointing to the server
- **Git LFS**: Required for `.omod` and `.sql` binary files

---

## Quick Start

```bash
# 1. Install dependencies
sudo apt-get update && sudo apt-get install -y git jq
curl -s https://packagecloud.io/install/repositories/github/git-lfs/script.deb.sh | sudo bash
sudo apt-get install -y git-lfs && git lfs install
# Install Docker CE: https://docs.docker.com/engine/install/ubuntu/

# 2. Initialize Docker Swarm
docker swarm init

# 3. Clone and fetch LFS files
git clone https://github.com/charess-org/sedish.git
cd sedish
git lfs pull

# 4. Configure
cp .env.hie .env
# Edit .env — set DOMAIN_NAME, SUBDOMAINS, RENEWAL_EMAIL

# 5. Build
./get-cli.sh linux
sudo mkdir -p /backups/elasticsearch /tmp/backups
./build-custom-images.sh
./build-image.sh

# 6. Deploy
./instant project init --env-file .env

# 7. Apply HAPI FHIR overrides (required after every deploy)
./packages/fhir-datastore-hapi-fhir/post-deploy.sh

# 8. Verify
docker service ls
```

> iSantePlus instances take **5–10 minutes** to fully boot. The data pipelines automatically wait for their respective iSantePlus instance to be ready before starting.

---

## Environment Configuration

Copy `.env.hie` to `.env` and configure at minimum:

| Variable | Description | Example |
|----------|-------------|---------|
| `DOMAIN_NAME` | Base domain for all services | `sedishtest.live` |
| `SUBDOMAINS` | Comma-separated list of all subdomains | `opencr.sedishtest.live,openhimconsole.sedishtest.live,...` |
| `RENEWAL_EMAIL` | Email for Let's Encrypt notifications | `admin@example.com` |
| `STAGING` | Set to `false` for production SSL certs | `false` |
| `INSECURE` | Set to `false` to enable HTTPS | `false` |

See `.env.hie` for the full list of configurable variables.

---

## Services & URLs

After deployment, the following services are accessible via HTTPS:

| Service | URL | Credentials |
|---------|-----|-------------|
| iSantePlus (HUEH) | `https://hueh.<domain>/openmrs` | `admin` / `Admin123` |
| iSantePlus (La Paix) | `https://lapaix.<domain>/openmrs` | `admin` / `Admin123` |
| iSantePlus (OFATMA) | `https://ofatma.<domain>/openmrs` | `admin` / `Admin123` |
| iSantePlus (Foyer St-Camille) | `https://foyer-saint-camille.<domain>/openmrs` | `admin` / `Admin123` |
| OpenHIM Console | `https://openhimconsole.<domain>` | `root@openhim.org` / `instant101` |
| OpenCR | `https://opencr.<domain>/crux` | — |
| SHR (HAPI FHIR Browser) | `https://shr.<domain>/fhir` | — |
| Grafana | `https://grafana.<domain>` | — |
| Keycloak | `https://keycloak.<domain>` | — |

---

## Package Management

Each HIE component is deployed as a package. Use the `instant` CLI to manage them:

```bash
# Deploy a package (first time)
./instant package init -n <package-name> --env-file .env

# Stop a package (preserves data)
./instant package down -n <package-name> --env-file .env

# Restart a package
./instant package up -n <package-name> --env-file .env
```

### Package Names

| Package ID | Stack | Description |
|------------|-------|-------------|
| `reverse-proxy-nginx` | `reverse-proxy` | Nginx + Let's Encrypt SSL |
| `interoperability-layer-openhim` | `openhim` | OpenHIM Core + Console + MongoDB |
| `fhir-datastore-hapi-fhir` | `hapi-fhir` | HAPI FHIR R4 Server (SHR) |
| `shared-health-record-fhir` | `shared-health-record` | SHR Mediator |
| `data-pipeline-isanteplus` | `pipeline` | FHIR Data Pipeline (4 instances) |
| `emr-isanteplus` | `isanteplus` | iSantePlus EMR instances |
| `client-registry-opencr` | `client-registry-opencr` | OpenCR MPI |
| `database-postgres` | `postgres` | PostgreSQL (HAPI FHIR, Keycloak) |
| `database-mysql` | `mysql` | MySQL (iSantePlus) |
| `identity-access-manager-keycloak` | `keycloak` | Keycloak SSO |
| `monitoring` | `monitoring` | Grafana + Prometheus + Loki |

### Important Notes

- **`init` vs `up`**: Use `init` only for first-time deployment or after wiping data. Use `up` for restarts.
- **HAPI FHIR**: Always run `./packages/fhir-datastore-hapi-fhir/post-deploy.sh` after deploying this package.
- **OpenHIM**: If MongoDB was wiped, use `init` (not `up`) to re-run the config importer.
- **Pipeline**: Config is injected via Docker configs. To update `application.yaml`, you must recreate the Docker config (see [Troubleshooting](#updating-pipeline-configuration)).

---

## Adding a New iSantePlus Instance

Each iSantePlus instance requires configuration across **5 components** to ensure patients from different facilities are not merged in OpenCR. Here's the complete checklist for adding instance N (e.g., `isanteplus5`):

### Step 1 — MySQL database

The `projects/isanteplus-db/initdb/10-create-dbs.sh` script automatically creates databases `openmrs`, `openmrs2`, ..., `openmrsN` on first boot. Set `OPENMRS_DB_COUNT` in `.env` to cover the number of instances.

The `20-configure-per-instance.sh` script sets per-instance global properties on fresh databases. For existing databases, the `post-start.sh` script (baked into the image) handles this automatically.

### Step 2 — OpenHIM client

Each instance needs its own OpenHIM client so OpenCR can distinguish patient sources. Add to `packages/interoperability-layer-openhim/importer/volume/openhim-import.json`:

```json
{
  "roles": ["emr"],
  "clientID": "isanteplusN",
  "name": "isanteplusN",
  "passwordAlgorithm": "sha512",
  "passwordSalt": "<salt>",
  "passwordHash": "<hash>"
}
```

Generate the hash (password = clientID):

```python
python3 -c "
import hashlib, os
password = 'isanteplusN'
salt = os.urandom(16).hex()
print(f'passwordSalt: {salt}')
print(f'passwordHash: {hashlib.sha512((password + salt).encode()).hexdigest()}')
"
```

For an **existing** deployment, also create the client via the API:

```bash
curl -sk -u 'root@openhim.org:instant101' -X POST \
  -H 'Content-Type: application/json' \
  -d '{"clientID":"isanteplusN","name":"isanteplusN","roles":["emr"],"passwordAlgorithm":"sha512","passwordSalt":"<salt>","passwordHash":"<hash>"}' \
  https://openhimcore.<domain>/clients
```

### Step 3 — iSantePlus docker-compose

Add the new service to `packages/emr-isanteplus/docker-compose.yml`, following the pattern of existing instances. Key differences per instance:

```yaml
isanteplusN:
  image: itechuw/docker-isanteplus-server:local-2
  environment:
    - OMRS_CONFIG_CONNECTION_URL=${OMRS_CONFIG_CONNECTION_URL_N}  # jdbc:mysql://mysql:3306/openmrsN
    - OMRS_CONFIG_CONNECTION_USERNAME=${OMRS_CONFIG_CONNECTION_USERNAME_N}
    - OMRS_CONFIG_CONNECTION_PASSWORD=${OMRS_CONFIG_CONNECTION_PASSWORD_N}
    - ISANTEPLUS_INSTANCE=isanteplusN   # CRITICAL: unique per instance
  volumes:
    - isanteplusN-data:/openmrs/data    # unique volume per instance
  networks:
    - public
    - reverse-proxy
    - mysql
    - openhim
```

The `ISANTEPLUS_INSTANCE` env var drives the `post-start.sh` script which automatically sets:

| Property | Value | Purpose |
|----------|-------|---------|
| `mpi-client.pid.local` | `http://isanteplusN/ws/fhir2/pid/openmrsid/` | Unique MPI identifier system |
| `mpi-client.msg.sendingApplication` | `isanteplusN` | Source tag in OpenCR |
| `mpi-client.security.authtoken` | `isanteplusN` | OpenHIM client auth |
| `fhir2.uriPrefix` | `http://isanteplusN.sedishtest.live/openmrs/fhir2` | Unique FHIR identifier systems |
| `xdssender.oshr.username` | `isanteplusN` | OpenHIM client for SHR push |
| `xdssender.oshr.password` | `isanteplusN` | Must match the OpenHIM client hash |

### Step 4 — Nginx reverse proxy

Add a server block to `packages/reverse-proxy-nginx/package-conf-secure/http-isanteplus-secure.conf`:

```nginx
server {
    listen 80;
    server_name  facilityname.*;
    location / { return 301 https://$host$request_uri; }
}
server {
    listen 443 ssl;
    server_name  facilityname.*;
    location / {
        resolver 127.0.0.11 valid=30s;
        set $upstream_isanteplusN isanteplusN;
        proxy_pass http://$upstream_isanteplusN:8080;
    }
}
```

Add the subdomain to `SUBDOMAINS` in `.env`:

```
SUBDOMAINS=...,facilityname.sedishtest.live
```

### Step 5 — Data pipeline

Add a new pipeline service to `packages/data-pipeline-isanteplus/docker-compose.yml`:

```yaml
streaming-pipeline-N:
  image: us-docker.pkg.dev/cloud-build-fhir/fhir-analytics/main:latest
  entrypoint: /app/config/wait-and-start.sh
  environment:
    - JAVA_OPTS=-Xms2g -Xmx2g
    - FLINK_CONF_DIR=/app/config
    - fhirdata.fhirServerUrl=http://isanteplusN:8080/openmrs/ws/fhir2/R4
    - fhirdata.dwhRootPrefix=/dwh/pipeline_DWHN
    - fhirdata.incrementalSchedule=0 N/5 * * * *   # stagger: offset by N minutes
  configs:
    - source: application_yaml
      target: /app/config/application.yaml
    - source: flink_conf_yaml
      target: /app/config/flink-conf.yaml
    - source: wait_and_start
      target: /app/config/wait-and-start.sh
      mode: 0755
  volumes:
    - pipeline-dwh-N:/dwh
  ports:
    - target: 8080
      published: 809N    # unique host port
      protocol: tcp
      mode: host
```

Add the volume under `volumes:` and redeploy:

```bash
docker stack rm pipeline && sleep 10
docker stack deploy -c packages/data-pipeline-isanteplus/docker-compose.yml pipeline
```

### Step 6 — .env variables

Add the database connection variables for the new instance:

```
OMRS_CONFIG_CONNECTION_URL_N=jdbc:mysql://mysql:3306/openmrsN?autoReconnect=true
OMRS_CONFIG_CONNECTION_USERNAME_N=openmrsN
OMRS_CONFIG_CONNECTION_PASSWORD_N=dev_password_only
```

Add the subdomain mapping:

```
SUBDOMAIN_CORE_ISANTEPLUSN=facilityname
```

### Why all this is needed

OpenCR determines patient identity using a combination of:
- **Source tag** (OpenHIM clientID) — which facility the patient came from
- **Identifier system + value** — e.g., `http://isanteplusN.../3-isanteplus-id` = `1000NG`

If two instances share the same clientID and identifier system, OpenCR treats patients with the same ID value (e.g., `1000NG`) as the same person and **overwrites** the record. Each instance must have unique values for all of these to prevent cross-facility patient merging.

---

## Data Pipeline

The FHIR Data Pipeline runs **4 instances** — one per iSantePlus site — syncing data to the SHR every 5 minutes.

| Pipeline | Source | Host Port |
|----------|--------|-----------|
| `streaming-pipeline` | isanteplus (HUEH) | 8095 |
| `streaming-pipeline-2` | isanteplus2 (La Paix) | 8096 |
| `streaming-pipeline-3` | isanteplus3 (OFATMA) | 8097 |
| `streaming-pipeline-4` | isanteplus4 (Foyer St-Camille) | 8098 |

All instances share a single `application.yaml` config, with Spring Boot environment variable overrides for `fhirdata.fhirServerUrl` and `fhirdata.dwhRootPrefix` per service.

### Triggering a Manual Full Run

The scheduled runs use **incremental** mode, which only syncs resources changed since the last run. If new patients aren't appearing in the SHR, trigger a **full** run to resync everything:

```bash
for port in 8095 8096 8097 8098; do
  curl -X POST "http://localhost:${port}/run?runMode=FULL"
done
```

> **When to run this:**
> - If incremental runs show "0 secs" despite new data existing
> - After adding new iSantePlus instances
>
> Note: after a fresh deployment, the pipeline automatically waits for iSantePlus to boot and runs a full sync on first start — no manual trigger needed.

### Resource Types Synced

`Patient`, `Encounter`, `Observation`, `Condition`, `AllergyIntolerance`, `MedicationRequest`, `Practitioner`, `Group`

---

## SSL/TLS Certificates

### Let's Encrypt (Default)

Certificates are automatically provisioned during `init` via Certbot. The `set-secure-mode.sh` script handles the ACME HTTP-01 challenge by temporarily scaling down nginx to free port 80.

- Active when `INSECURE=false` and `USE_PROVIDED_CERTIFICATES=false` in `.env`
- Rate limit: 5 certificates per exact domain set per 7 days

### Provided Certificates (User-Supplied)

1. Place `fullchain.pem` and `privkey.pem` on the host
2. Set paths in `.env`:
   ```
   USE_PROVIDED_CERTIFICATES=true
   HOST_PROVIDED_CERT_FULLCHAIN_PATH=/ssl/your_domain/fullchain.pem
   HOST_PROVIDED_CERT_PRIVKEY_PATH=/ssl/your_domain/privkey.pem
   ```
3. Rebuild: `./build-image.sh`
4. Redeploy: `./instant package init -n reverse-proxy-nginx --env-file .env`

---

## Redeployment Scenarios

### Redeploy a single package

```bash
./instant package down -n <package-name> --env-file .env
./instant package up -n <package-name> --env-file .env

# If HAPI FHIR:
./packages/fhir-datastore-hapi-fhir/post-deploy.sh
```

### Rebuild and redeploy after code changes

```bash
# If iSantePlus Dockerfile or modules changed:
docker build -t itechuw/docker-isanteplus-server:local-2 packages/emr-isanteplus/

# If other images changed:
./build-custom-images.sh

# Force the service to use the new image:
docker service update --force --image <image>:<tag> <stack>_<service>
```

### Full teardown and redeploy

This is the most common scenario — stopping everything and restarting. Here's the exact procedure with all post-deploy steps:

```bash
# 1. Stop all services
./instant project down --env-file .env

# 2. Rebuild images (if code changed)
docker build -t itechuw/docker-isanteplus-server:local-2 packages/emr-isanteplus/
./build-image.sh

# 3. Deploy
./instant project init --env-file .env

# 4. Apply HAPI FHIR overrides
./packages/fhir-datastore-hapi-fhir/post-deploy.sh

# 5. Redeploy the pipeline (instant tooling can't update Docker configs in-place)
docker stack rm pipeline && sleep 10
docker stack deploy -c packages/data-pipeline-isanteplus/docker-compose.yml pipeline

# 6. Verify
docker service ls --format '{{.Name}} {{.Replicas}}' | grep '0/' | grep -v 'await-helper\|config-importer'
```

#### Expected errors during `project init` (safe to ignore)

| Error | Reason | Impact |
|-------|--------|--------|
| OpenHIM config importer fails with "Incorrect credentials" | MongoDB data persists across `down`/`init` — the password was already changed from default on a previous init | None — channels/clients are already configured |
| Pipeline "Wrong configuration" | Docker configs are immutable; the instant tooling can't update existing configs with new content | Fixed by step 5 above (`docker stack rm` + `deploy`) |
| ElasticSearch "Failed to set elastic passwords" | Passwords already set from previous init | None — ES is running |
| LNSP mediator timeout | Non-critical lab integration service | None for core HIE |

#### Post-deploy verification checklist

```bash
# All services should be 1/1 (except await-helper and config-importer at 0/1)
docker service ls

# SSL cert should show real Let's Encrypt issuer (not STAGING)
docker exec $(docker ps -q -f name=reverse-proxy_reverse-proxy-nginx) \
  openssl x509 -in /run/secrets/fullchain.pem -noout -issuer -dates

# HAPI FHIR overrides should be set
docker service inspect hapi-fhir_hapi-fhir \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}' \
  | grep -E 'referential|client_id|server_address'

# OpenHIM should have channels configured
docker exec $(docker ps -q -f name=openhim_openhim-core.1) \
  curl -sk -u 'root@openhim.org:instant101' https://localhost:8080/channels \
  | python3 -c "import sys,json; print(f'{len(json.load(sys.stdin))} channels')"

# Wait for iSantePlus to boot (~5-10 min), then verify per-instance config
for svc in isanteplus isanteplus2 isanteplus3; do
  pid=$(docker exec $(docker ps -q -f name=isanteplus_${svc}.1) \
    curl -s -u 'admin:Admin123' \
    "http://localhost:8080/openmrs/ws/rest/v1/systemsetting/mpi-client.pid.local" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('value','NOT READY'))" 2>/dev/null)
  echo "$svc: $pid"
done

# Trigger full pipeline sync (after iSantePlus is fully booted)
for port in 8095 8096 8097 8098; do
  curl -X POST "http://localhost:${port}/run?runMode=FULL"
done

# Verify SHR is accessible
curl -s https://shr.<your-domain>/fhir/Patient | head -c 100
```

### Complete wipe (destroys all data)

```bash
sudo bash purge-local.sh
# Then follow Quick Start from step 5
```

> After a complete wipe, `project init` will succeed without the "Incorrect credentials" error because the MongoDB is fresh.

---

## Troubleshooting

### Which services are down?

```bash
docker service ls --format '{{.Name}} {{.Replicas}}' | grep '0/'
```

> Services at `0/1` that are **expected**: `await-helper`, `config-importer`. Everything else should be `1/1`.

### Check service logs

```bash
docker service logs <service_name> --tail 50
```

### SHR (HAPI FHIR browser) returns 502

HAPI FHIR needs to be on the `reverse-proxy_public` network for nginx to reach it:

```bash
docker service update --network-add reverse-proxy_public hapi-fhir_hapi-fhir
```

> This is already in `docker-compose.yml` but may not be applied if the instant tooling overrides the service spec.

### Check SSL certificate

```bash
docker exec $(docker ps -q -f name=reverse-proxy_reverse-proxy-nginx) \
  openssl x509 -in /run/secrets/fullchain.pem -noout -issuer -dates
```

If issuer contains `(STAGING)`, redeploy nginx to get a real certificate.

### OpenHIM MongoDB "NotWritablePrimary"

```bash
# Initialize replica set
docker exec $(docker ps -q -f name=openhim_mongo-1) mongo --eval \
  'rs.initiate({_id:"mongo-set",members:[{_id:0,host:"mongo-1:27017"}]})'

# Re-import channels/clients
./instant package init -n interoperability-layer-openhim --env-file .env
```

### HAPI FHIR "database does not exist"

```bash
docker exec $(docker ps -q -f name=postgres_postgres-1) \
  env PGPASSWORD=instant101 psql -U postgres -c 'CREATE DATABASE hapi;'
docker service update --force hapi-fhir_hapi-fhir
```

### HAPI FHIR missing configuration overrides

```bash
docker service inspect hapi-fhir_hapi-fhir \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}' \
  | grep -E 'referential|client_id'
```

If missing, run `./packages/fhir-datastore-hapi-fhir/post-deploy.sh`.

### Pipeline fails to deploy ("Wrong configuration")

Docker configs are immutable. If you changed `application.yaml`, `flink-conf.yaml`, or `wait-and-start.sh`, the existing Docker config can't be updated in-place:

```bash
docker stack rm pipeline
sleep 10
docker stack deploy -c packages/data-pipeline-isanteplus/docker-compose.yml pipeline
```

### Pipeline authentication failure

```bash
# Check sink credentials
docker exec $(docker ps -q -f name=pipeline_streaming-pipeline.1) \
  cat /app/config/application.yaml | grep -A2 sink

# Test auth
docker exec $(docker ps -q -f name=pipeline_streaming-pipeline.1) \
  curl -s -u 'shr-pipeline:instant101' http://openhim-core:5001/SHR/fhir/metadata | head -c 100
```

### OpenCR shows fewer patients than expected

Check if per-instance identifiers are set (all should be different):

```bash
for svc in isanteplus isanteplus2 isanteplus3; do
  docker exec $(docker ps -q -f name=isanteplus_${svc}.1) \
    curl -s -u 'admin:Admin123' \
    "http://localhost:8080/openmrs/ws/rest/v1/systemsetting/mpi-client.pid.local" \
    | python3 -c "import sys,json; print('$svc:', json.load(sys.stdin).get('value','NOT SET'))"
done
```

If they're all the same, the `post-start.sh` didn't run yet (iSantePlus still booting) or the `ISANTEPLUS_INSTANCE` env var is missing. Wait 5-10 minutes and check again.

### iSantePlus returns 404 on /openmrs

OpenMRS is still in its initial setup phase. It takes **5-10 minutes** to fully boot (module loading, liquibase migrations, Spring context refresh). Check progress:

```bash
docker service logs isanteplus_isanteplus --tail 5
```

Look for `Refreshing Context` or `Started OpenMRS` — those indicate boot is nearly complete.

### Force restart a stuck service

```bash
docker service update --force <service_name>
```

---

## OpenHIM Client Password Management

OpenHIM clients authenticate on port 5001 using Basic auth with SHA512-hashed passwords.

**Generate a password hash:**

```python
python3 -c "
import hashlib, os
password = 'instant101'
salt = os.urandom(16).hex()
hash_val = hashlib.sha512((password + salt).encode()).hexdigest()
print(f'passwordSalt: {salt}')
print(f'passwordHash: {hash_val}')
"
```

> Formula: `sha512(password + salt)` — **password first, then salt**.

**Files that must stay in sync:**

| File | Field |
|------|-------|
| `packages/data-pipeline-isanteplus/config/application.yaml` | `sinkUserName` / `sinkPassword` |
| `packages/interoperability-layer-openhim/importer/volume/openhim-import.json` | Client `passwordHash` / `passwordSalt` |

---

## Security Considerations

- **Docker Secrets**: Use for sensitive configuration (passwords, API keys)
- **Swarm Locking**: Rotate the CA key with `docker swarm ca --rotate`
- **SSH Hardening**: Key-based auth only, disable root login
- **AWS Security Groups**: Restrict inbound traffic to ports 80, 443 only
- **Firewall**: Use iptables/nftables to whitelist necessary connections
- **Encryption**: Enable EBS/RDS encryption with KMS-managed keys
- **Monitoring**: Enable CloudWatch and GuardDuty for threat detection

---

## Project Structure

```
sedish/
├── .env                          # Environment configuration
├── build-custom-images.sh        # Builds iSantePlus, MySQL, ES images
├── build-image.sh                # Builds the management/deployment image
├── get-cli.sh                    # Downloads the Instant OpenHIE CLI
├── instant                       # Instant OpenHIE CLI binary
├── SETUP-GUIDE.md                # Quick reference setup guide
├── packages/
│   ├── reverse-proxy-nginx/      # Nginx + Let's Encrypt
│   ├── interoperability-layer-openhim/  # OpenHIM
│   ├── fhir-datastore-hapi-fhir/ # HAPI FHIR + post-deploy.sh
│   ├── shared-health-record-fhir/ # SHR Mediator
│   ├── data-pipeline-isanteplus/ # FHIR Data Pipeline (4 instances)
│   ├── emr-isanteplus/           # iSantePlus EMR
│   ├── client-registry-opencr/   # OpenCR
│   ├── database-postgres/        # PostgreSQL
│   ├── database-mysql/           # MySQL
│   ├── identity-access-manager-keycloak/ # Keycloak
│   ├── monitoring/               # Grafana + Prometheus + Loki
│   └── lnsp-mediator/            # LNSP lab integration
└── projects/
    ├── isanteplus-db/            # MySQL seed data for iSantePlus
    ├── lnsp-mediator/            # LNSP mediator source
    └── lnsp-analytics/           # LNSP analytics dashboard
```

---

## Additional Resources

- [Instant OpenHIE v2 Documentation](https://jembi.gitbook.io/instant-v2)
- [Jembi Platform](https://github.com/jembi/platform)
- [OpenHIM Documentation](http://openhim.org/docs/)
- [HAPI FHIR Documentation](https://hapifhir.io/hapi-fhir/docs/)
- [OpenCR Documentation](https://intrahealth.github.io/client-registry/)
- [iSantePlus Wiki](https://wiki.openmrs.org/display/RES/iSantePlus)
- [FHIR Data Pipes](https://github.com/google/fhir-data-pipes)
