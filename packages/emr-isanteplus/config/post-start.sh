#!/bin/bash
# Post-start configuration for iSantePlus.
# Runs in the background after OpenMRS boots and sets global properties
# that need to match the deployment environment.
#
# Optional env vars:
#   OPENHIM_DOMAIN - OpenHIM domain (default: openhimcore.sedishtest.live)

DOMAIN="${OPENHIM_DOMAIN:-openhimcore.sedishtest.live}"
OPENMRS_USER="${OPENMRS_ADMIN_USER:-admin}"
OPENMRS_PASS="${OPENMRS_ADMIN_PASS:-Admin123}"
OPENMRS_URL="http://localhost:8080/openmrs"

echo "[post-start] Waiting for OpenMRS to be ready..."

MAX_WAIT=900
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
  echo "[post-start] ERROR: OpenMRS did not become ready after ${MAX_WAIT}s."
  exit 1
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

# XDS-Sender endpoints — point to the correct OpenHIM domain
set_property "xdssender.exportCcdEndpoint" "https://${DOMAIN}/SHR/fhir"
set_property "xdssender.mpiEndpoint" "https://${DOMAIN}/CR/fhir"
set_property "xdssender.oshr.password" "isanteplus"
set_property "xdssender.oshr.username" "isanteplus"

echo "[post-start] Configuration complete."
