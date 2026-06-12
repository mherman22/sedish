#!/usr/bin/env bash
# Build the two local images and (re)deploy the SEDISH "hie" stack.
# `docker stack deploy` does not build, so we build first.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> ensuring submodule is checked out"
git -C .. submodule update --init projects/consolidated-fhir-mapper

echo "==> building cdc-reader image"
docker build -t consolidated-cdc-reader:local ../projects/consolidated-server

echo "==> building fhir-pipeline image (SQLMesh + loader, from the submodule)"
docker build -t consolidated-fhir-mapper:local ../projects/consolidated-fhir-mapper

echo "==> deploying stack 'hie'"
# shellcheck disable=SC1091
set -a; [ -f .env ] && . ./.env; set +a
docker stack deploy -c docker-compose.yml hie

echo "==> services:"
docker stack services hie
echo
echo "consolidated-db first boot loads the 44-table schema (~2-4 min)."
echo "Tail:  docker service logs -f hie_cdc-reader   /   docker service logs -f hie_fhir-pipeline"
