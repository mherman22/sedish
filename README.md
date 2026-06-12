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
│ iSantePlus  │──mpi-client───▶│   Port 5001     │                └──────────────┘
│ (Site N)    │                 └─────────────────┘
└─────────────┘
```

**Data flows:**

| Flow | Trigger | Path | Purpose |
|------|---------|------|---------|
| Patient identity | Patient create/update | iSantePlus → OpenHIM → OpenCR | Real-time MPI registration via `mpi-client` module |
| Clinical documents | Lab order (VL/EID) | iSantePlus → OpenHIM → SHR → HAPI FHIR | Real-time via `xds-sender` module |

---

## Components

| Component | Image | Purpose |
|-----------|-------|---------|
| [iSantePlus](https://github.com/IsantePlus/openmrs-distro-isanteplus) | `itechuw/docker-isanteplus-server:local-2` | OpenMRS-based EMR (multiple clinic instances) |
| [OpenHIM](http://openhim.org/) | `jembi/openhim-core:v8.5.0` | Interoperability layer — routes, logs, and secures all data exchange |
| [OpenCR](https://github.com/intrahealth/client-registry) | `itechuw/opencr` | Master Patient Index (MPI) — de-duplicates patient identities |
| [HAPI FHIR](https://hapifhir.io/) | `jembi/hapi:v7.0.3-wget` | FHIR R4 data store — Shared Health Record (SHR) |
| [SHR Mediator](https://github.com/DIGI-UW/shared-health-record) | `itechuw/shared-health-record:main` | Proxies FHIR requests to HAPI FHIR with validation |
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
# Edit .env — set DOMAIN_NAME, SUBDOMAINS, RENEWAL_EMAIL, STAGING=false

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

> iSantePlus instances take **10–15 minutes** to fully boot on first start. The `post-start.sh` script automatically configures xds-sender endpoints once OpenMRS is ready.

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

> Note: iSantePlus serves on `/openmrs`, not `/`. Going to `https://hueh.<domain>/` will return 404.

---

## Patient ID Uniqueness Across Facilities

Each iSantePlus instance has its own MySQL database (`openmrs`, `openmrs2`, `openmrs3`, ...) initialized from the same SQL dump. To prevent patient ID collisions in OpenCR, two mechanisms are used:

1. **Sequence offsets**: Each database starts its idgen sequence at a different value (instance 1 at 100000, instance 2 at 200000, etc.) so generated IDs never overlap.

2. **Unique FHIR system URIs**: Each instance has a unique `mpi-client.pid.local` value (e.g., `http://hueh.sedishtest.live/ws/fhir2/pid/openmrsid/`) so OpenCR can distinguish patient sources even if IDs were to collide.

Both are configured automatically by `projects/isanteplus-db/initdb/20-configure-per-instance.sh` during fresh database initialization. The `FACILITY_NAMES` env var controls the mapping (default: `hueh,lapaix,ofatma,fsc`).

**Patient ID format**: iSantePlus uses the Luhn Mod-30 check digit validator with the character set `0123456789ACDEFGHJKLMNPRTUVWXY`. Note that B, I, O, Q, S, Z are deliberately excluded to avoid ambiguity.

---

## Deployment

There are two ways to deploy: the **Instant CLI** (recommended) or **manual `docker stack deploy`** (for development or when you need direct control).

### Option A: Instant CLI (recommended)

The Instant CLI reads `package-metadata.json` for env var injection and handles Docker config management. **You must rebuild the management image after any local changes:**

```bash
# Build the management image (required after any local file changes)
./build-image.sh

# Deploy everything at once
./instant project init --env-file .env

# Post-deploy fixes (Instant CLI doesn't handle these)
./packages/fhir-datastore-hapi-fhir/post-deploy.sh
```

Or deploy per-package in dependency order:

```bash
./instant package init -n database-postgres --env-file .env
./instant package init -n database-mysql --env-file .env
./instant package init -n interoperability-layer-openhim --env-file .env
./instant package init -n reverse-proxy-nginx --env-file .env
./instant package init -n fhir-datastore-hapi-fhir --env-file .env
./packages/fhir-datastore-hapi-fhir/post-deploy.sh
./instant package init -n client-registry-opencr --env-file .env
./instant package init -n shared-health-record-fhir --env-file .env
./instant package init -n emr-isanteplus --env-file .env
# EMR → SHR is the consolidated pipeline (separate `hie` stack): cd hie && ./deploy.sh
```

### Option B: Manual deployment (docker stack)

Uses local files directly — no `./build-image.sh` needed. However, env vars from `package-metadata.json` won't be substituted (use for packages that don't depend on them, or set the vars in `.env`).

```bash
# 1. Databases (no dependencies)
docker stack deploy -c packages/database-postgres/docker-compose.yml postgres
docker stack deploy -c packages/database-mysql/docker-compose.yml mysql

# 2. Interoperability layer (bundles its own MongoDB)
docker stack deploy -c packages/interoperability-layer-openhim/docker-compose.yml \
  -c packages/interoperability-layer-openhim/docker-compose.config.yml openhim

# 3. Reverse proxy
docker stack deploy -c packages/reverse-proxy-nginx/docker-compose.yml reverse-proxy

# 4. FHIR datastore (needs postgres)
docker stack deploy -c packages/fhir-datastore-hapi-fhir/docker-compose.yml hapi-fhir
./packages/fhir-datastore-hapi-fhir/post-deploy.sh

# 5. Client Registry (needs its own ES + HAPI FHIR + postgres)
docker stack deploy \
  -c packages/client-registry-opencr/docker-compose-es.yml \
  -c packages/client-registry-opencr/docker-compose-postgres.yml \
  -c packages/client-registry-opencr/docker-compose-hapi.yml \
  -c packages/client-registry-opencr/docker-compose.yml \
  client-registry-opencr

# 6. SHR Mediator (needs openhim + hapi-fhir)
docker stack deploy -c packages/shared-health-record-fhir/docker-compose.yml shared-health-record

# 7. iSantePlus EMR (needs mysql + openhim — takes 10-15 min to boot)
docker stack deploy -c packages/emr-isanteplus/docker-compose.yml isanteplus

# 8. EMR → SHR: the consolidated → FHIR pipeline (separate `hie` stack — see hie/README.md)
cd hie && ./deploy.sh && cd ..

# 9. Verify all services are up
docker service ls --format 'table {{.Name}}\t{{.Replicas}}'
```

> **Note:** OpenCR may need a force restart after first deploy if ES wasn't ready:
> `docker service update --force client-registry-opencr_opencr`

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
| `emr-isanteplus` | `isanteplus` | iSantePlus EMR instances |
| `client-registry-opencr` | `client-registry-opencr` | OpenCR MPI |
| `database-postgres` | `postgres` | PostgreSQL (HAPI FHIR, Keycloak) |
| `database-mysql` | `mysql` | MySQL (iSantePlus) |
| `identity-access-manager-keycloak` | `keycloak` | Keycloak SSO |
| `monitoring` | `monitoring` | Grafana + Prometheus + Loki |

### Important Notes

- **`init` vs `up`**: Use `init` only for first-time deployment or after wiping data. Use `up` for restarts.
- **HAPI FHIR**: Always run `./packages/fhir-datastore-hapi-fhir/post-deploy.sh` after deploying or updating HAPI FHIR. This adds the `reverse-proxy_public` network (for SHR browser access) and sets referential integrity + placeholder target settings. The instant CLI reads compose files from the jembi/platform base image, which doesn't include our HAPI FHIR overrides — this script applies them.
- **OpenHIM**: If MongoDB was wiped, use `init` (not `up`) to re-run the config importer.

---

## EMR → SHR pipeline

The EMR → SHR path is the **consolidated → FHIR pipeline** — the `hie` stack (consolidated
server + `openmrs-fhir-sqlmesh`): the consolidated server ingests the iSantePlus EMRs (CDC),
SQLMesh maps `consolidated_db` to FHIR, and a loader pushes to OpenCR (identity) + SHR
(clinical). See [`hie/README.md`](hie/README.md). It replaced the earlier per-instance
pipeline that mirrored the EMR FHIR API directly to the SHR.

```bash
cd hie && ./deploy.sh
docker stack services hie
docker service logs -f hie_fhir-pipeline
```

---

## Adding a New iSantePlus Instance

Each iSantePlus instance requires configuration across multiple components. Here's the checklist for adding instance N (e.g., `isanteplus5`):

### Step 1 — MySQL database

The `projects/isanteplus-db/initdb/10-create-dbs.sh` script automatically creates databases `openmrs`, `openmrs2`, ..., `openmrsN` on first boot. Set `OPENMRS_DB_COUNT` in `.env` to cover the number of instances.

The `20-configure-per-instance.sh` script runs on fresh database init and sets:
- xds-sender endpoints pointing to your OpenHIM domain
- idgen sequence offset (instance N starts at N * 100000)
- Unique `mpi-client.pid.local` FHIR system URI per facility

Add the new facility to the `FACILITY_NAMES` env var (comma-separated, lowercase).

### Step 2 — iSantePlus docker-compose

Add the new service to `packages/emr-isanteplus/docker-compose.yml`:

```yaml
isanteplusN:
  image: itechuw/docker-isanteplus-server:local-2
  environment:
    - OMRS_CONFIG_CONNECTION_URL=${OMRS_CONFIG_CONNECTION_URL_N}
    - OMRS_CONFIG_CONNECTION_USERNAME=${OMRS_CONFIG_CONNECTION_USERNAME_N}
    - OMRS_CONFIG_CONNECTION_PASSWORD=${OMRS_CONFIG_CONNECTION_PASSWORD_N}
    # ... (copy remaining OMRS env vars from existing instances)
  volumes:
    - isanteplusN-data:/openmrs/data
    - /etc/timezone:/etc/timezone:ro
    - /etc/localtime:/etc/localtime:ro
  networks:
    - public
    - reverse-proxy
    - mysql
    - openhim
```

Add the volume under `volumes:` and the database variables to `package-metadata.json`.

### Step 3 — Nginx reverse proxy

Add a server block to `packages/reverse-proxy-nginx/package-conf-secure/`:

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

Add the subdomain to `SUBDOMAINS` in `.env`.

### Step 4 — .env variables

```
OMRS_CONFIG_CONNECTION_URL_N=jdbc:mysql://mysql:3306/openmrsN?autoReconnect=true
OMRS_CONFIG_CONNECTION_USERNAME_N=openmrsN
OMRS_CONFIG_CONNECTION_PASSWORD_N=dev_password_only
SUBDOMAIN_CORE_ISANTEPLUSN=facilityname
```

---

## SSL/TLS Certificates

### How it works

Certificates are provisioned during `init` via Certbot. The `set-secure-mode.sh` script:
1. Generates a staging (dummy) cert to bootstrap nginx
2. Scales nginx down to free port 80
3. Generates a production cert via HTTP-01 challenge
4. Updates nginx Docker secrets with the real cert and scales back up

### Rate limits

Let's Encrypt allows **5 certificates per exact domain set per 7 days**. If you hit this limit, you'll see:

```
too many certificates (5) already issued for this exact set of identifiers
```

**Workaround**: Request a cert with a different subset of domains (a different "exact set" is not rate-limited):

```bash
# Scale down nginx to free port 80
docker service scale reverse-proxy_reverse-proxy-nginx=0

# Request cert with fewer domains
docker run --rm --network host --name certbot \
  -v prod-certbot-conf:/etc/letsencrypt/archive/${DOMAIN_NAME} \
  certbot/certbot:v1.23.0 certonly -n --standalone \
  -m admin@${DOMAIN_NAME} \
  -d "${DOMAIN_NAME},subdomain1.${DOMAIN_NAME},subdomain2.${DOMAIN_NAME}" \
  --agree-tos

# Copy certs and create Docker secrets
docker run --rm --network host -w /temp \
  -v prod-certbot-conf:/temp-certificates \
  -v instant:/temp busybox sh \
  -c "rm -rf certificates; mkdir -p certificates; cp -r /temp-certificates/* /temp/certificates"

TIMESTAMP=$(date "+%Y%m%d%H%M%S")
docker secret create --label name=nginx "${TIMESTAMP}-fullchain.pem" \
  <(docker run --rm -v instant:/temp busybox cat /temp/certificates/fullchain1.pem)
docker secret create --label name=nginx "${TIMESTAMP}-privkey.pem" \
  <(docker run --rm -v instant:/temp busybox cat /temp/certificates/privkey1.pem)

# Swap secrets on nginx and scale back up
CURR_FULL=$(docker service inspect reverse-proxy_reverse-proxy-nginx \
  --format '{{(index .Spec.TaskTemplate.ContainerSpec.Secrets 0).SecretName}}')
CURR_KEY=$(docker service inspect reverse-proxy_reverse-proxy-nginx \
  --format '{{(index .Spec.TaskTemplate.ContainerSpec.Secrets 1).SecretName}}')

docker service update --replicas 1 \
  --secret-rm "$CURR_FULL" --secret-rm "$CURR_KEY" \
  --secret-add source=${TIMESTAMP}-fullchain.pem,target=/run/secrets/fullchain.pem \
  --secret-add source=${TIMESTAMP}-privkey.pem,target=/run/secrets/privkey.pem \
  reverse-proxy_reverse-proxy-nginx

# Clean up
docker volume rm prod-certbot-conf
```

### User-supplied certificates

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

# Force the service to pick up the new image:
docker service update --force isanteplus_isanteplus
```

### Full teardown and redeploy

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

# 5. Verify
docker service ls --format '{{.Name}} {{.Replicas}}' | grep '0/' | grep -v 'await-helper\|config-importer'
```

### Complete wipe (destroys all data)

```bash
sudo bash purge-local.sh
# Then follow Quick Start from step 5
```

---

## Troubleshooting

### Quick diagnostics

```bash
# Which services are down?
docker service ls --format '{{.Name}} {{.Replicas}}' | grep '0/'
# Expected at 0/1: await-helper, config-importer. Everything else should be 1/1.

# Check logs for a specific service
docker service logs <service_name> --tail 50

# Check logs for a specific container (faster for large log histories)
docker logs $(docker ps -q -f name=<service_name>.1) --tail 50

# Force restart a stuck service
docker service update --force <service_name>

# Check what networks a service is on
docker inspect $(docker ps -q -f name=<service_name>) \
  --format '{{range $net, $conf := .NetworkSettings.Networks}}{{$net}} {{end}}'

# Check env vars on a running service
docker service inspect <service_name> \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}'

# Check env vars inside a running container
docker exec $(docker ps -q -f name=<service_name>.1) env | grep <VAR>
```

### iSantePlus returns 404

OpenMRS is still booting. First boot takes **10–15 minutes** (module loading, liquibase migrations, Spring context refresh). Check progress:

```bash
docker logs $(docker ps -q -f name=isanteplus_isanteplus.1) --tail 10
```

- `Refreshing Context` — still loading, almost done
- `DispatcherServlet.noHandlerFound` — REST API not mapped yet, still refreshing
- `referenceapplication.started value: true` — boot complete

If it's stuck, check the container uptime:

```bash
docker ps --format "{{.Names}}\t{{.Status}}" | grep isanteplus
```

### iSantePlus container restarts every ~7 minutes

The base image `startup.sh` uses `wait ${!}` which waits for the last background process (post-start.sh), not Tomcat. When post-start.sh completes, the container exits. This is fixed in the custom Dockerfile which captures `TOMCAT_PID=$!` and uses `wait $TOMCAT_PID`.

If the container is still restarting, rebuild the image:

```bash
docker build -t itechuw/docker-isanteplus-server:local-2 packages/emr-isanteplus/
docker service update --force isanteplus_isanteplus
```

### iSantePlus post-start.sh not running or timing out

```bash
# Check post-start output for a specific instance
docker logs $(docker ps -q -f name=isanteplus_isanteplus.1) 2>&1 | grep "\[post-start\]"
```

- `Waiting for OpenMRS to be ready...` — still polling, OpenMRS not ready yet
- `OpenMRS is ready (after Xs)` — success, then check for `Set xdssender.*` lines
- `ERROR: OpenMRS did not become ready after 900s` — timed out; OpenMRS may need more time or has an error

If post-start timed out, check if OpenMRS is actually running:

```bash
docker exec $(docker ps -q -f name=isanteplus_isanteplus.1) \
  curl -sf -u admin:Admin123 http://localhost:8080/openmrs/ws/rest/v1/session
```

### Patient creation fails with "Select a preferred identifier"

The idgen auto-generation is not working. Check the idgen configuration:

```bash
# Check idgen sequence config
docker exec $(docker ps -q -f name=mysql_mysql) \
  mysql -u openmrs -pdev_password_only openmrs -e \
  "SELECT prefix, next_sequence_value FROM idgen_seq_id_gen WHERE id = 1;"

# Check auto-generation is enabled
docker exec $(docker ps -q -f name=mysql_mysql) \
  mysql -u openmrs -pdev_password_only openmrs -e \
  "SELECT * FROM idgen_auto_generation_option;"
```

The `automatic_generation_enabled` column must be `1` and `source` must point to a valid `idgen_identifier_source`.

### OpenCR merges/overwrites patients from different facilities

Check that each instance has a unique `mpi-client.pid.local`:

```bash
for svc in isanteplus isanteplus2 isanteplus3 isanteplus4; do
  echo -n "$svc: "
  docker exec $(docker ps -q -f name=isanteplus_${svc}.1) \
    curl -s -u admin:Admin123 \
    "http://localhost:8080/openmrs/ws/rest/v1/systemsetting/mpi-client.pid.local" 2>/dev/null \
    | grep -o '"value":"[^"]*"' | sed 's/"value":"//;s/"//'
done
```

If they're all the same (e.g., `http://isanteplus/ws/fhir2/pid/openmrsid/`), the `20-configure-per-instance.sh` didn't run. Fix manually:

```bash
# Replace <db>, <facility>, <domain> for each instance
docker exec $(docker ps -q -f name=mysql_mysql) \
  mysql -u <db> -pdev_password_only <db> -e \
  "UPDATE global_property SET property_value = 'http://<facility>.<domain>/ws/fhir2/pid/openmrsid/'
   WHERE property = 'mpi-client.pid.local';"
```

Then restart the iSantePlus instances to pick up the change.

### SSL certificate shows staging issuer

```bash
# Check current certificate
docker exec $(docker ps -q -f name=reverse-proxy_reverse-proxy-nginx) \
  openssl x509 -in /run/secrets/fullchain.pem -noout -issuer -subject -dates
```

If issuer contains `(STAGING)`, the production cert generation failed. Common causes:
- **Port 80 was not free** — nginx wasn't scaled down before certbot ran
- **Rate limited** — too many certs issued in the last 7 days
- **DNS not pointing to server** — the domain must resolve to this server's IP

See the [SSL/TLS Certificates](#ssltls-certificates) section for the manual fix.

### SSL certificate doesn't cover a subdomain

Check the SANs on the current cert:

```bash
docker exec $(docker ps -q -f name=reverse-proxy_reverse-proxy-nginx) \
  openssl x509 -in /run/secrets/fullchain.pem -noout -text | grep "DNS:"
```

If a subdomain is missing, regenerate the cert with the correct domain list (see rate limit workaround above).

### Browser shows HSTS error (can't bypass)

Firefox/Zen will refuse to load a page with a staging cert if HSTS is enabled. The only fix is to get a valid production certificate. In Chrome, you can:
1. Go to `chrome://net-internals/#hsts`
2. Delete the domain under "Delete domain security policies"
3. Revisit and click "Advanced → Proceed"

### SHR (HAPI FHIR browser) returns 502

HAPI FHIR needs to be on the `reverse-proxy_public` network:

```bash
docker service update --network-add reverse-proxy_public hapi-fhir_hapi-fhir
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

### OpenHIM MongoDB "NotWritablePrimary"

```bash
# Check replica set status
docker exec $(docker ps -q -f name=openhim_mongo-1) mongo --eval "rs.status()"

# Initialize replica set
docker exec $(docker ps -q -f name=openhim_mongo-1) mongo --eval \
  'rs.initiate({_id:"mongo-set",members:[{_id:0,host:"mongo-1:27017"}]})'

# Restart OpenHIM Core
docker service update --force openhim_openhim-core

# Re-import channels/clients (if MongoDB was wiped)
./instant package init -n interoperability-layer-openhim --env-file .env
```

### Instant CLI deploys with empty env vars

If you run `docker stack deploy` directly (instead of via `./instant`), the `${OMRS_CONFIG_*}` variables in docker-compose.yml will resolve to empty strings because they come from `package-metadata.json`, which the instant CLI reads.

**Always use the instant CLI to deploy packages:**

```bash
./instant package init -n emr-isanteplus --env-file .env
```

If you need to update a single service's env var without redeploying:

```bash
docker service update --env-add KEY=VALUE <service_name>
```

### Checking and updating idgen sequences directly

```bash
# Check all databases
for db in openmrs openmrs2 openmrs3 openmrs4; do
  echo "=== $db ==="
  docker exec $(docker ps -q -f name=mysql_mysql) \
    mysql -u "$db" -pdev_password_only "$db" -e \
    "SELECT prefix, next_sequence_value FROM idgen_seq_id_gen WHERE id = 1;" 2>&1 | grep -v Warning
done

# Update a specific database's sequence offset
docker exec $(docker ps -q -f name=mysql_mysql) \
  mysql -u openmrs2 -pdev_password_only openmrs2 -e \
  "UPDATE idgen_seq_id_gen SET next_sequence_value = 200000 WHERE id = 1;"
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
| `packages/interoperability-layer-openhim/importer/volume/openhim-import.json` | Client `passwordHash` / `passwordSalt` |
| `hie/.env` (loader creds) | `OPENCR_*` / `SHR_*` for the consolidated pipeline |

---

## Security Considerations

- **Default passwords**: Change `Admin123`, `instant101`, `dev_password_only` before production use
- **Docker Secrets**: Use for sensitive configuration (passwords, API keys)
- **Swarm Locking**: Rotate the CA key with `docker swarm ca --rotate`
- **SSH Hardening**: Key-based auth only, disable root login
- **Firewall**: Restrict inbound traffic to ports 80, 443 only
- **Encryption**: Enable disk encryption for data at rest

---

## Project Structure

```
sedish/
├── .env                          # Environment configuration
├── build-custom-images.sh        # Builds iSantePlus, MySQL, ES images
├── build-image.sh                # Builds the management/deployment image
├── get-cli.sh                    # Downloads the Instant OpenHIE CLI
├── instant                       # Instant OpenHIE CLI binary
├── packages/
│   ├── reverse-proxy-nginx/      # Nginx + Let's Encrypt
│   ├── interoperability-layer-openhim/  # OpenHIM
│   ├── fhir-datastore-hapi-fhir/ # HAPI FHIR + post-deploy.sh
│   ├── shared-health-record-fhir/ # SHR Mediator
│   ├── emr-isanteplus/           # iSantePlus EMR
│   │   ├── Dockerfile            # Custom image with post-start.sh
│   │   ├── config/post-start.sh  # Auto-configures xds-sender on boot
│   │   └── docker-compose.yml    # Service definitions for all instances
│   ├── client-registry-opencr/   # OpenCR
│   ├── database-postgres/        # PostgreSQL
│   ├── database-mysql/           # MySQL
│   ├── identity-access-manager-keycloak/ # Keycloak
│   └── monitoring/               # Grafana + Prometheus + Loki
├── hie/                         # EMR → SHR consolidated → FHIR pipeline stack
│   ├── docker-compose.yml       # consolidated-db + cdc-reader + kafka + fhir-pipeline
│   └── deploy.sh
└── projects/
    ├── isanteplus-db/           # MySQL seed data for iSantePlus
    ├── consolidated-server/     # CDC reader → consolidated_db (production schema)
    └── openmrs-fhir-sqlmesh/    # submodule: SQLMesh consolidated_db→FHIR + loader
```

---

## Additional Resources

- [Instant OpenHIE v2 Documentation](https://jembi.gitbook.io/instant-v2)
- [Jembi Platform](https://github.com/jembi/platform)
- [OpenHIM Documentation](http://openhim.org/docs/)
- [HAPI FHIR Documentation](https://hapifhir.io/hapi-fhir/docs/)
- [OpenCR Documentation](https://intrahealth.github.io/client-registry/)
- [iSantePlus Wiki](https://wiki.openmrs.org/display/RES/iSantePlus)
