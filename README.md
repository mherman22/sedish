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

> iSantePlus instances take **5–10 minutes** to fully boot. Wait for the login page to load before testing data flows.

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
> - After initial deployment (to create the DWH baseline)
> - After adding new iSantePlus instances
> - If incremental runs show "0 secs" despite new data existing
> - After any pipeline redeployment (`docker stack rm pipeline && docker stack deploy ...`)

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
./build-custom-images.sh
docker service update --force --image <image>:<tag> <stack>_<service>
```

### Full teardown and redeploy

```bash
./instant project down --env-file .env
./instant project init --env-file .env
./packages/fhir-datastore-hapi-fhir/post-deploy.sh
```

### Complete wipe (destroys all data)

```bash
sudo bash purge-local.sh
# Then follow Quick Start from step 5
```

---

## Troubleshooting

### Which services are down?

```bash
docker service ls --format '{{.Name}} {{.Replicas}}' | grep '0/'
```

> `await-helper` services at `0/1` are normal.

### Check service logs

```bash
docker service logs <service_name> --tail 50
```

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

### Pipeline authentication failure

```bash
# Check sink credentials
docker exec $(docker ps -q -f name=pipeline_streaming-pipeline.1) \
  cat /app/config/application.yaml | grep -A2 sink

# Test auth
docker exec $(docker ps -q -f name=pipeline_streaming-pipeline.1) \
  curl -s -u 'shr-pipeline:instant101' http://openhim-core:5001/SHR/fhir/metadata | head -c 100
```

### Updating pipeline configuration

Docker configs are immutable. To update `application.yaml`:

```bash
docker stack rm pipeline
# Edit config files, then redeploy:
docker stack deploy -c packages/data-pipeline-isanteplus/docker-compose.yml pipeline
```

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
