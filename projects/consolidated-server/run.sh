#!/usr/bin/env bash
# Build the CDC reader image and (re)deploy the consolidated-server stack.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Building cdc-reader image"
docker build -t consolidated-cdc-reader:local .

echo "==> Deploying stack 'consolidated'"
# shellcheck disable=SC1091
set -a; [ -f .env ] && . ./.env; set +a
docker stack deploy -c docker-compose.yml consolidated

echo "==> Services:"
docker stack services consolidated
echo
echo "Tail the reader:  docker service logs -f consolidated_cdc-reader"
