#!/bin/bash
# Post-start configuration for iSantePlus instances.
# Runs in the background after OpenMRS boots and sets per-instance
# global properties that cannot be baked into the shared MySQL dump.
#
# Required env vars:
#   ISANTEPLUS_INSTANCE - service name (e.g., isanteplus, isanteplus2)
#   OPENHIM_DOMAIN      - OpenHIM domain (default: sedishtest.live)

INSTANCE="${ISANTEPLUS_INSTANCE:-isanteplus}"
DOMAIN="${OPENHIM_DOMAIN:-openhimcore.sedishtest.live}"
OPENMRS_USER="${OPENMRS_ADMIN_USER:-admin}"
OPENMRS_PASS="${OPENMRS_ADMIN_PASS:-Admin123}"
OPENMRS_URL="http://localhost:8080/openmrs"

echo "[post-start] Waiting for OpenMRS to be ready..."

MAX_WAIT=600
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
  STATUS=$(curl -sf -o /dev/null -w "%{http_code}" -u "${OPENMRS_USER}:${OPENMRS_PASS}" \
    "${OPENMRS_URL}/ws/rest/v1/session" 2>/dev/null || echo "000")
  if [ "$STATUS" = "200" ]; then
    echo "[post-start] OpenMRS is ready (after ${WAITED}s)"
    break
  fi
  sleep 10
  WAITED=$((WAITED + 10))
done

if [ $WAITED -ge $MAX_WAIT ]; then
  echo "[post-start] WARNING: OpenMRS did not become ready after ${MAX_WAIT}s. Skipping configuration."
  exit 0
fi

set_property() {
  local prop="$1"
  local value="$2"
  curl -sf -u "${OPENMRS_USER}:${OPENMRS_PASS}" \
    -X POST -H 'Content-Type: application/json' \
    -d "{\"value\":\"${value}\"}" \
    "${OPENMRS_URL}/ws/rest/v1/systemsetting/${prop}" > /dev/null 2>&1
  echo "[post-start] Set ${prop} = ${value}"
}

# Unique MPI identifier per instance (prevents OpenCR from merging patients across sites)
set_property "mpi-client.pid.local" "http://${INSTANCE}/ws/fhir2/pid/openmrsid/"

# OpenHIM endpoints for xds-sender
set_property "xdssender.exportCcdEndpoint" "https://${DOMAIN}/SHR/fhir"
set_property "xdssender.mpiEndpoint" "https://${DOMAIN}/CR/fhir"
set_property "xdssender.oshr.password" "isanteplus"

echo "[post-start] Configuration complete for instance: ${INSTANCE}"
