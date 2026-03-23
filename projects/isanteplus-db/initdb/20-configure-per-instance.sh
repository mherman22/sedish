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
OPENHIM_DOMAIN="${OPENHIM_DOMAIN:-openhimcore.sedishtest.live}"

for i in $(seq 1 "$OPENMRS_DB_COUNT"); do
  if [ "$i" -eq 1 ]; then
    db="openmrs"
  else
    db="openmrs$i"
  fi

  echo "Configuring $db: xds-sender endpoints → ${OPENHIM_DOMAIN}"

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
EOSQL

done

echo "Database configuration complete."
