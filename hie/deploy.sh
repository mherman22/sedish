#!/usr/bin/env bash
# (Re)deploy the SEDISH "hie" stack. The pipeline image is a published GHCR package
# (built by the sedish-fhir-pipeline repo's CI) — nothing is built or vendored here.
set -euo pipefail
cd "$(dirname "$0")"

# CONSOLIDATED_HOST/USER/PASS (the EXTERNAL Consolidé MySQL) must be set — see .env.example
# shellcheck disable=SC1091
set -a; [ -f .env ] && . ./.env; set +a

# The image package is private (private repo). Authenticate once on this node, e.g.:
#   echo "$GHCR_PAT" | docker login ghcr.io -u <user> --password-stdin
# --with-registry-auth forwards that auth to the swarm workers that pull the image.
echo "==> deploying stack 'hie' (pulls ghcr.io/mherman22/sedish-fhir-pipeline:${PIPELINE_TAG:-main})"
docker stack deploy --with-registry-auth -c docker-compose.yml hie

echo "==> services:"
docker stack services hie
echo
echo "Tail:  docker service logs -f hie_fhir-pipeline"
