#!/bin/bash
# Wrapper entrypoint for fhir-data-pipes that waits for the FHIR source
# (iSantePlus) to be ready before starting the pipeline. This prevents
# the pipeline from creating a corrupt/empty DWH baseline when iSantePlus
# is still booting, which would cause all subsequent incremental runs to
# report "0 secs" with no data synced.

set -e

# Extract fhirServerUrl from env var override first, then config file
FHIR_URL="${fhirdata_fhirServerUrl:-}"
if [ -z "$FHIR_URL" ]; then
  FHIR_URL=$(grep 'fhirServerUrl' /app/config/application.yaml | head -1 | sed 's/.*"\(.*\)".*/\1/')
fi

# No auth needed — the gateway proxy injects credentials
echo "Waiting for FHIR source to be ready: $FHIR_URL"

MAX_WAIT=600  # 10 minutes max
WAITED=0
INTERVAL=10

while [ $WAITED -lt $MAX_WAIT ]; do
  RESPONSE=$(curl -sf -o /dev/null -w "%{http_code}" "${FHIR_URL}/metadata" 2>/dev/null || echo "000")

  if [ "$RESPONSE" = "200" ]; then
    echo "FHIR source is ready (HTTP 200 after ${WAITED}s)"
    break
  fi

  echo "FHIR source not ready (HTTP $RESPONSE), retrying in ${INTERVAL}s... (${WAITED}/${MAX_WAIT}s)"
  sleep $INTERVAL
  WAITED=$((WAITED + INTERVAL))
done

if [ $WAITED -ge $MAX_WAIT ]; then
  echo "ERROR: FHIR source did not become ready after ${MAX_WAIT}s. Exiting."
  exit 1
fi

# Check if a DWH baseline exists. If not, the first scheduled run will
# automatically be a full sync. Do NOT clear existing DWH on restart —
# that would force a multi-hour re-sync of all data.
DWH_PREFIX="${fhirdata_dwhRootPrefix:-}"
if [ -z "$DWH_PREFIX" ]; then
  DWH_PREFIX=$(grep 'dwhRootPrefix' /app/config/application.yaml | head -1 | sed 's/.*"\(.*\)".*/\1/')
fi

if [ -n "$DWH_PREFIX" ] && [ -d "/dwh" ] && [ -z "$(ls -A /dwh/ 2>/dev/null)" ]; then
  echo "No DWH baseline found — first run will be a full sync"
else
  echo "Existing DWH found — incremental runs will resume"
fi

# Ensure Spring Boot reads from our config directory
export SPRING_CONFIG_LOCATION="file:/app/config/application.yaml"

# Hand off to the original entrypoint
exec /docker-entrypoint.sh
