#!/bin/bash
#
# Run this after deploying fhir-datastore-hapi-fhir to apply HAPI FHIR
# configuration that docker stack deploy doesn't pick up from docker-compose.yml.
#
# Usage:
#   ./instant package up -n fhir-datastore-hapi-fhir --env-file .env
#   ./packages/fhir-datastore-hapi-fhir/post-deploy.sh
#
# Or combined:
#   ./instant package up -n fhir-datastore-hapi-fhir --env-file .env && \
#     ./packages/fhir-datastore-hapi-fhir/post-deploy.sh

set -e

DOMAIN_NAME="${DOMAIN_NAME:-sedishtest.live}"

echo "Applying HAPI FHIR configuration overrides..."
docker service update --detach \
  --env-add "hapi.fhir.enforce_referential_integrity_on_write=false" \
  --env-add "hapi.fhir.auto_create_placeholder_reference_targets=true" \
  --env-add "hapi.fhir.allow_multiple_delete=true" \
  --env-add "hapi.fhir.client_id_strategy=ANY" \
  --env-add "hapi.fhir.server_address=https://shr.${DOMAIN_NAME}/fhir" \
  hapi-fhir_hapi-fhir

echo "Done. HAPI FHIR will restart with the updated configuration."
