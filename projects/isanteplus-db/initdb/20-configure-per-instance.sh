#!/bin/bash
set -e

# This script runs after 10-create-dbs.sh and fixes global properties
# that have incorrect defaults in the shared SQL dump.
#
# The SQL dump ships with xds-sender endpoints pointing to the old
# production domain (openhim.sedish-haiti.org). This script updates
# them to point to the correct OpenHIM domain for this deployment.

if [ -z "$OPENMRS_DB_COUNT" ]; then
  OPENMRS_DB_COUNT=10
fi

if [ -z "$MYSQL_ROOT_PASSWORD" ]; then
  echo "MYSQL_ROOT_PASSWORD is not set."
  exit 1
fi

# Default endpoint domain — override via OPENHIM_DOMAIN env var
OPENHIM_DOMAIN="${OPENHIM_DOMAIN:-openhimcore.haitihie.uwdigi.org}"

# Map DB index → facility subdomain. Override via FACILITY_NAMES env var
# (comma-separated, e.g. "hueh,lapaix,ofatma,fsc").
IFS=',' read -ra FACILITIES <<< "${FACILITY_NAMES:-hueh,lapaix,ofatma,fsc}"
DOMAIN_NAME="${DOMAIN_NAME:-haitihie.uwdigi.org}"

# Each facility gets its own ID range (offset by 100000) to prevent
# collisions in the MPI. No prefix needed — the Luhn Mod-30 check digit
# only allows characters in 0123456789ACDEFGHJKLMNPRTUVWXY.
SEQUENCE_OFFSET=100000

for i in $(seq 1 "$OPENMRS_DB_COUNT"); do
  if [ "$i" -eq 1 ]; then
    db="openmrs"
  else
    db="openmrs$i"
  fi

  idx=$((i - 1))
  FACILITY="${FACILITIES[$idx]:-site${i}}"
  START_SEQ=$((i * SEQUENCE_OFFSET))

  echo "Configuring $db: facility=${FACILITY}, start_seq=${START_SEQ}"

  mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$db" <<EOSQL
-- Fix xds-sender endpoints (dump has old production domain)
UPDATE global_property SET property_value = 'https://${OPENHIM_DOMAIN}/SHR/fhir'
  WHERE property = 'xdssender.exportCcdEndpoint';
UPDATE global_property SET property_value = 'https://${OPENHIM_DOMAIN}/CR/fhir'
  WHERE property = 'xdssender.mpiEndpoint';
UPDATE global_property SET property_value = 'isanteplus'
  WHERE property = 'xdssender.oshr.password';
UPDATE global_property SET property_value = 'isanteplus'
  WHERE property = 'xdssender.oshr.username';
-- Offset idgen sequence so each facility generates unique IDs (no prefix)
UPDATE idgen_seq_id_gen SET prefix = NULL, next_sequence_value = ${START_SEQ} WHERE id = 1;
-- Unique FHIR system URI per facility so OpenCR distinguishes sources
UPDATE global_property SET property_value = 'http://${FACILITY}.${DOMAIN_NAME}/ws/fhir2/pid/openmrsid/'
  WHERE property = 'mpi-client.pid.local';
EOSQL

done

echo "Database configuration complete."
