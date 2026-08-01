#!/bin/bash


# TEMP: Ensure the /tmp/backups folder exists
mkdir /tmp/backups

# Make sure jq is installed; install it if not
if ! command -v jq &> /dev/null; then
  echo "jq could not be found"
  sudo apt-get install jq
fi

docker build \
    -t lnsp-mediator:local \
    -f projects/lnsp-mediator/Dockerfile \
    projects/lnsp-mediator \

# Build lnsp-analytics image
docker build \
    -t lnsp-analytics:local \
    -f projects/lnsp-analytics/Dockerfile \
    projects/lnsp-analytics \

# Load Env vars from json file environmentVariables field
filepath="./packages/emr-isanteplus/package-metadata.json"
envs=$(jq -r '.environmentVariables | to_entries | .[] | "\(.key)=\(.value)"' $filepath)

# Export each environment variable
while IFS= read -r line; do
  export "$line"
done <<< "$envs"

# docker compose \
#     -f packages/emr-isanteplus/docker-compose.yml \
#     build

##
# lnsp migrations
##
filepath="./packages/lnsp-mediator/package-metadata.json"
envs=$(jq -r '.environmentVariables | to_entries | .[] | "\(.key)=\(.value)"' $filepath)

# Export each environment variable
while IFS= read -r line; do
  export "$line"
done <<< "$envs"

migrate_mongo_config_js_DIGEST="$(cat ./packages/lnsp-mediator/importer/migrate-mongo-config.js | md5sum | cut -d ' ' -f1)"
export migrate_mongo_config_js_DIGEST
package_json_DIGEST="$(cat ./packages/lnsp-mediator/importer/package.json | md5sum | cut -d ' ' -f1)"
export package_json_DIGEST

docker compose \
    -f packages/lnsp-mediator/importer/docker-compose.migrate.yml \
    build

##
# Isanteplus DB
##
# Load Env vars from package-metadata.json file
# filepath="./packages/database-mysql/package-metadata.json"
# envs=$(jq -r '.environmentVariables | to_entries | .[] | "\(.key)=\(.value)"' $filepath)

# Export each environment variable
while IFS= read -r line; do
  export "$line"
done <<< "$envs"

# Build the Docker image
docker build -t isanteplus-mysql:5.7.44 ./projects/isanteplus-db

# Build custom Elasticsearch image with phonetic + string-similarity-scoring plugins
docker build -t docker.elastic.co/elasticsearch/elasticsearch:local ./packages/analytics-datastore-elastic-search

# Build the OpenCR Client Registry Elasticsearch image: ES 7.9.1 + string-similarity-scoring v0.0.6
# (the plugin that EXECUTES the decision-rule matching algorithms). Vendored plugin zip, so it's
# reproducible and replaces the floating intrahealth/elasticsearch:latest. Referenced as
# opencr-es is now pulled as itechuw/elasticsearch-opencr:develop (same ES 7.9.1 +
# analysis-phonetic + string-similarity-scoring 0.0.6 recipe, published by the fork CI).


# Build the SEDISH FHIR pipeline image locally from its (public) repo.
# The pipeline lives in its own repo (not vendored here); clone/refresh then build.
PIPELINE_SRC=".build/sedish-fhir-pipeline"
if [ -d "$PIPELINE_SRC/.git" ]; then
  git -C "$PIPELINE_SRC" pull --ff-only || true
else
  git clone --depth 1 https://github.com/mherman22/sedish-fhir-pipeline.git "$PIPELINE_SRC"
fi
docker build -t sedish-fhir-pipeline:local "$PIPELINE_SRC"

# Build the FHIR Router mediator image locally from its repo (private — clone via gh auth).
# Only needed when FHIR_ROUTER_IMAGE=fhir-router-mediator:local; the default pulls from GHCR.
ROUTER_SRC=".build/fhir-router-mediator"
if [ -d "$ROUTER_SRC/.git" ]; then
  git -C "$ROUTER_SRC" pull --ff-only || true
else
  gh repo clone mherman22/fhir-router-mediator "$ROUTER_SRC" -- --depth 1 \
    || git clone --depth 1 https://github.com/mherman22/fhir-router-mediator.git "$ROUTER_SRC"
fi
docker build -t fhir-router-mediator:local "$ROUTER_SRC"

# Build the Shared Health Record (SHR) image locally from its repo (private — clone via gh auth),
# develop branch. Only needed when SHR_IMAGE=shared-health-record:local; the default pulls
# itechuw/shared-health-record:develop from Docker Hub.
SHR_SRC=".build/shared-health-record"
if [ -d "$SHR_SRC/.git" ]; then
  git -C "$SHR_SRC" fetch --depth 1 origin develop && git -C "$SHR_SRC" reset --hard origin/develop || true
else
  gh repo clone DIGI-UW/shared-health-record "$SHR_SRC" -- --depth 1 --branch develop \
    || git clone --depth 1 --branch develop https://github.com/DIGI-UW/shared-health-record.git "$SHR_SRC"
fi
docker build -t shared-health-record:local "$SHR_SRC"
