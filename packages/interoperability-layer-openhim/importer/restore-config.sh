#!/usr/bin/env bash
#
# Seed OpenHIM's channels, clients and users straight into Mongo from openhim-import.json.
#
# The packaged config importer POSTs the same file through the OpenHIM API as root@openhim.org. Since
# Keycloak took over identity in v8 that account has no password hash, so the importer gets 401 and exits
# 1 on every deploy — which is survivable while the Mongo volume persists and fatal the moment it does
# not. This applies the same file without the API.
#
# Usage:  ./restore-config.sh [mongo-container]
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPORT_FILE="${HERE}/volume/openhim-import.json"
CONTAINER="${1:-$(docker ps --format '{{.ID}} {{.Names}}' | awk '/openhim_mongo/ {print $1; exit}')}"

if [ -z "${CONTAINER}" ]; then
  echo "No OpenHIM mongo container found. Pass one: ./restore-config.sh <container>" >&2
  exit 1
fi
if [ ! -f "${IMPORT_FILE}" ]; then
  echo "Missing ${IMPORT_FILE}" >&2
  exit 1
fi

echo "Restoring OpenHIM config from ${IMPORT_FILE} into container ${CONTAINER}"
docker cp "${IMPORT_FILE}" "${CONTAINER}:/tmp/openhim-import.json"

docker exec "${CONTAINER}" mongo openhim --quiet --eval '
var d = JSON.parse(cat("/tmp/openhim-import.json"));
var now = new Date();
var added = { users: 0, passports: 0, clients: 0, channels: 0 };

(d.Users || []).forEach(function (u) {
  if (db.users.findOne({ email: u.email })) { return; }
  u.created = now; u.updated = now;
  db.users.insertOne(u); added.users++;
});

(d.Passports || []).forEach(function (p) {
  if (db.passports.findOne({ email: p.email, protocol: p.protocol })) { return; }
  db.passports.insertOne(p); added.passports++;
});

// Clients carry their own salt and hash, so basic auth keeps working without knowing the passwords.
(d.Clients || []).forEach(function (c) {
  if (db.clients.findOne({ clientID: c.clientID })) { return; }
  db.clients.insertOne(c); added.clients++;
});

var owner = db.users.findOne({}) || {};
(d.Channels || []).forEach(function (c) {
  if (db.channels.findOne({ name: c.name, urlPattern: c.urlPattern })) { return; }
  // Fields the API would have stamped on POST.
  c.updatedBy = c.updatedBy || {
    id: owner._id,
    name: owner.firstname ? (owner.firstname + " " + owner.surname) : "config importer"
  };
  if (c.lastBodyCleared === undefined) { c.lastBodyCleared = null; }
  db.channels.insertOne(c); added.channels++;
});

print("added   users=" + added.users + "  passports=" + added.passports +
      "  clients=" + added.clients + "  channels=" + added.channels);
print("totals  users=" + db.users.count() + "  clients=" + db.clients.count() +
      "  channels=" + db.channels.count());
'

echo
echo "Mediators re-register themselves on startup, which also restores their stored config"
echo "(OpenCR's matching.boostMode among it):"
echo "  docker service update --force client-registry-opencr_opencr"
echo "  docker service update --force shared-health-record_shr"
