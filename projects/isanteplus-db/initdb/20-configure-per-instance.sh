#!/bin/bash
set -e

# This script runs after 10-create-dbs.sh and configures per-instance
# settings for the xds-sender and mpi-client OpenMRS modules.
#
# Each iSantePlus instance needs:
# - A unique mpi-client.pid.local URI so OpenCR treats patients from
#   different facilities as separate records
# - Correct xds-sender endpoints for the deployment environment
# - Correct OpenHIM client password

if [ -z "$OPENMRS_DB_COUNT" ]; then
  OPENMRS_DB_COUNT=10
fi

if [ -z "$MYSQL_ROOT_PASSWORD" ]; then
  echo "MYSQL_ROOT_PASSWORD is not set."
  exit 1
fi

# Default endpoint domain — override via OPENHIM_DOMAIN env var
OPENHIM_DOMAIN="${OPENHIM_DOMAIN:-openhimcore.sedishtest.live}"

# Map instance numbers to facility names for ID prefixes.
# These must match the SUBDOMAIN_CORE_ISANTEPLUS* values in .env.
FACILITY_NAMES="HUEH LAPAIX OFATMA FSC KERITAJ OSONAPI GRESSIER PESTEL STDG BETHEL"

for i in $(seq 1 "$OPENMRS_DB_COUNT"); do
  if [ "$i" -eq 1 ]; then
    db="openmrs"
    instance="isanteplus"
  else
    db="openmrs$i"
    instance="isanteplus$i"
  fi

  # Get the facility name for this instance (fallback to instance number)
  facility=$(echo "$FACILITY_NAMES" | awk "{print \$$i}")
  if [ -z "$facility" ]; then
    facility="SITE$i"
  fi

  echo "Configuring per-instance settings for $db (instance: $instance)"

  mysql -u root -p"$MYSQL_ROOT_PASSWORD" "$db" <<EOSQL
-- Unique identifier system per instance (prevents OpenCR from merging patients across sites)
UPDATE global_property SET property_value = 'http://${instance}/ws/fhir2/pid/openmrsid/'
  WHERE property = 'mpi-client.pid.local';

-- XDS Sender endpoints (point to the correct OpenHIM domain)
UPDATE global_property SET property_value = 'https://${OPENHIM_DOMAIN}/SHR/fhir'
  WHERE property = 'xdssender.exportCcdEndpoint';
UPDATE global_property SET property_value = 'https://${OPENHIM_DOMAIN}/CR/fhir'
  WHERE property = 'xdssender.mpiEndpoint';

-- OpenHIM client password (must match the 'isanteplus' client hash in openhim-import.json)
UPDATE global_property SET property_value = 'isanteplus'
  WHERE property = 'xdssender.oshr.password';

-- Unique ID prefix per instance (prevents all instances from generating the same
-- iSantePlus IDs like 1000NG, which causes OpenCR to merge patients across sites).
-- e.g., HUEH-1000NG, LAPAIX-1000NG, OFATMA-1000NG
UPDATE idgen_seq_id_gen SET prefix = '${facility}-' WHERE id = 1;
EOSQL

done

echo "Per-instance configuration complete."
