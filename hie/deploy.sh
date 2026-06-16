#!/usr/bin/env bash
# Build the pipeline image and (re)deploy the SEDISH "hie" stack.
# `docker stack deploy` does not build, so we build first.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> ensuring submodule is checked out"
git -C .. submodule update --init projects/consolidated-fhir-mapper

echo "==> building fhir-pipeline image (SQLMesh + loader, from the submodule)"
docker build -t consolidated-fhir-mapper:local ../projects/consolidated-fhir-mapper

echo "==> deploying stack 'hie'"
# CONSOLIDATED_HOST/USER/PASS (the EXTERNAL Consolidé MySQL) must be set — see .env.example
# shellcheck disable=SC1091
set -a; [ -f .env ] && . ./.env; set +a
docker stack deploy -c docker-compose.yml hie

echo "==> services:"
docker stack services hie
echo
echo "Tail:  docker service logs -f hie_fhir-pipeline"
