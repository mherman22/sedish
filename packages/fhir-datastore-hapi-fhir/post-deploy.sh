#!/bin/bash
# Run after deploying fhir-datastore-hapi-fhir to apply settings
# that the jembi/platform base image doesn't include.
#
# The instant CLI reads compose files from the platform image, not from
# our local overrides. This script applies the missing settings.

set -e

echo "Applying HAPI FHIR post-deploy settings..."

# Add reverse-proxy network (needed for SHR browser at shr.<domain>/fhir)
docker service update --network-add reverse-proxy_public hapi-fhir_hapi-fhir 2>/dev/null || true

# Ensure referential integrity + placeholder targets are set correctly
docker service update \
  --env-add "hapi.fhir.enforce_referential_integrity_on_write=true" \
  --env-add "hapi.fhir.auto_create_placeholder_reference_targets=true" \
  --env-add "hapi.fhir.allow_external_references=true" \
  hapi-fhir_hapi-fhir 2>/dev/null || true

echo "Done."
