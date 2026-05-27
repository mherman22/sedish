#!/bin/bash
# ---------------------------------------------------------------------------
# SEDISH HIE — Newman Integration Test Runner
#
# Usage:
#   .postman/run-tests.sh [environment-file]
#
# Assumes the HIE is already running (e.g. via `instant project init`).
# Defaults to the CI environment if no file is provided.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-$SCRIPT_DIR/environments/ci.postman_environment.json}"
RESULTS_DIR="$SCRIPT_DIR/results"

mkdir -p "$RESULTS_DIR"

# --- Pre-flight checks -----------------------------------------------------

if ! command -v newman &> /dev/null; then
    echo "ERROR: Newman is not installed."
    echo "       Install it with:  npm install -g newman"
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: Environment file not found: $ENV_FILE"
    exit 1
fi

# --- Wait for OpenHIM to be ready (max 120 s) ------------------------------

BASE_URL=$(node -e "
  var env = require('$ENV_FILE');
  var v = env.values.find(function(v){ return v.key === 'baseUrl'; });
  console.log(v ? v.value : 'http://localhost:5001');
")

echo "Waiting for OpenHIM at $BASE_URL ..."
for i in $(seq 1 24); do
    if curl -sf -o /dev/null "$BASE_URL/" 2>/dev/null; then
        echo "OpenHIM is responding."
        break
    fi
    if [ "$i" -eq 24 ]; then
        echo "ERROR: OpenHIM not responding after 120 seconds."
        exit 1
    fi
    sleep 5
done

# --- Run collections in order -----------------------------------------------

COLLECTIONS_DIR="$SCRIPT_DIR/collections"
EXIT_CODE=0

for collection in "$COLLECTIONS_DIR"/*.postman_collection.json; do
    name="$(basename "$collection")"
    echo ""
    echo "=========================================="
    echo "  Running: $name"
    echo "=========================================="

    newman run "$collection" \
        --environment "$ENV_FILE" \
        --insecure \
        --reporters cli,junit \
        --reporter-junit-export "$RESULTS_DIR/${name%.json}-results.xml" \
        || EXIT_CODE=$?
done

# --- Summary -----------------------------------------------------------------

echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "All collections passed."
else
    echo "Some collections failed (exit code $EXIT_CODE)."
fi

echo "JUnit results saved to: $RESULTS_DIR/"
exit $EXIT_CODE
