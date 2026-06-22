#!/bin/bash
# Post-start configuration for iSantePlus.
# Runs in the background after OpenMRS boots and sets global properties
# that need to match the deployment environment.
#
# Required env vars:
#   FACILITY_ID   - unique facility identifier (e.g., hueh, lapaix)
#   FACILITY_NAME - human-readable facility name (e.g., HUEH, La Paix)
#
# Leaf mode: the EMR does NOT write to the HIE. Ingestion is via CHARESS binlog CDC ->
# consolidated_db -> pipeline -> OpenCR + SHR. We empty the write endpoints and keep only the
# read-only PDQ search. See docs/emr-leaf-mode.md.

FACILITY="${FACILITY_ID:-isanteplus}"
CR_ENDPOINT="${CR_ENDPOINT:-http://openhim-core:5001/CR/fhir}"
OPENMRS_USER="${OPENMRS_ADMIN_USER:-admin}"
OPENMRS_PASS="${OPENMRS_ADMIN_PASS:-Admin123}"
OPENMRS_URL="http://localhost:8080/openmrs"

echo "[post-start] Facility: ${FACILITY_ID} (${FACILITY_NAME})"
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

# Disable all EMR -> HIE writes (empty endpoints override the module config.xml defaults).
set_property "mpi-client.endpoint.cr.addr"  ""   # santedb-mpiclient PIX feed (patient/encounter save)
set_property "mpi-client.endpoint.pix.addr" ""   # PIX cross-reference write-back (import path)
set_property "xdssender.exportCcdEndpoint"  ""   # xds-sender CCD push to SHR (>= 2.6.1 skips cleanly)

# Keep read-only MPI search (PDQ) + its per-facility OpenHIM client credentials.
set_property "mpi-client.endpoint.pdq.addr"      "${CR_ENDPOINT}"
set_property "mpi-client.msg.sendingApplication" "${FACILITY}"
set_property "mpi-client.security.authtoken"     "${FACILITY}"

echo "[post-start] Configuration complete."
