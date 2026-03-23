#!/bin/bash
# Post-start configuration for iSantePlus.
# Runs in the background after OpenMRS boots and sets global properties
# that need to match the deployment environment and facility identity.
#
# Required env vars:
#   FACILITY_NAME  - facility prefix for patient IDs (e.g., HUEH, LAPAIX)
#   OPENHIM_DOMAIN - OpenHIM domain (default: openhimcore.sedishtest.live)

FACILITY="${FACILITY_NAME:-SITE1}"
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

# Set the "Facility ID Prefix" attribute on the default location.
# This drives the idgen LocationBasedPrefixProvider to generate
# facility-specific patient IDs (e.g., HUEH1000NG, LAPAIX1000NG).
# Note: no python3 in the container, so we parse JSON with grep/sed.
LOC_UUID=$(curl -s -u "${OPENMRS_USER}:${OPENMRS_PASS}" \
  "${OPENMRS_URL}/ws/rest/v1/location?limit=1" 2>/dev/null | \
  grep -o '"uuid":"[^"]*"' | head -1 | sed 's/"uuid":"//;s/"//' || echo "")

if [ -n "$LOC_UUID" ]; then
  curl -sf -u "${OPENMRS_USER}:${OPENMRS_PASS}" \
    -X POST -H 'Content-Type: application/json' \
    -d "{\"attributeType\":\"d4f5e8a1-9b3c-4e7f-a2d6-1c8b9e0f3a5d\",\"value\":\"${FACILITY}\"}" \
    "${OPENMRS_URL}/ws/rest/v1/location/${LOC_UUID}/attribute" > /dev/null 2>&1
  echo "[post-start] Set Facility ID Prefix = ${FACILITY} on location ${LOC_UUID}"
else
  echo "[post-start] WARNING: Could not find default location to set facility prefix"
fi

echo "[post-start] Configuration complete."
