"""
Consolidated-server -> SHR publisher (CDC-triggered fhir2 fetch).

This is the production-grade publisher chosen over hand-rolling OpenMRS->FHIR:
  * The phase-1 binlog CDC tells us WHICH patients changed (high-water mark on
    the consolidated tables' _synced_at), exactly as before.
  * For each changed patient we pull that patient's FHIR straight from the
    owning EMR's OpenMRS fhir2 API (Patient + clinical resources), which already
    produces SHR-grade FHIR (real CIEL codings, identifier systems, all resource
    types) — so we get fhir2 parity for free instead of reimplementing fhir2.
  * We wrap the fetched resources in a transaction Bundle (PUT = idempotent) and
    POST to OpenHIM's SHR passthrough, which MPI-enriches via OpenCR and stores
    in HAPI.

Change detection stays decoupled (binlog), conversion is delegated to fhir2.

Runs once and exits, or loops every PUBLISH_INTERVAL seconds.
"""
import base64
import json
import os
import sys
import time
import logging
import urllib.request
import urllib.error

# reuse the consolidated-DB + high-water-mark machinery from the phase-2 publisher
from publisher import (
    connect_dst, rows, ensure_publish_state, load_high_water, save_high_water,
    changed_patient_keys,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("publisher-fhir2")


def env(name, default=None):
    return os.environ.get(name, default)


SHR_URL = env("SHR_URL", "http://openhim-core:5001/SHR/fhir").rstrip("/")
SHR_USER = env("SHR_USER", "shr-pipeline")
SHR_PASS = env("SHR_PASS", "instant101")
EMR_USER = env("EMR_USER", "admin")
EMR_PASS = env("EMR_PASS", "Admin123")
# fhir2 base URL template per facility db. `{host}` is derived from the schema
# name: openmrs -> isanteplus, openmrs2 -> isanteplus2, openmrsN -> isanteplusN.
EMR_FHIR_TEMPLATE = env("EMR_FHIR_TEMPLATE", "http://{host}:8080/openmrs/ws/fhir2/R4")
PUBLISH_INTERVAL = int(env("PUBLISH_INTERVAL", "0"))
PAGE_SIZE = int(env("PAGE_SIZE", "200"))
# patient-scoped clinical resources to pull, matching the EMR pipeline's coverage
PATIENT_RESOURCES = [r.strip() for r in env(
    "PATIENT_RESOURCES",
    "Encounter,Observation,Condition,AllergyIntolerance,MedicationRequest",
).split(",") if r.strip()]

# --- MPI (OpenCR) enrollment: the fingerprint/identity -> MPI path ---
# Pushing the Patient (which carries ALL its identifiers, including the biometric
# national reference code, type 6) to OpenCR enrolls it and triggers matching:
# OpenCR decision rule 1 (Biometric) and rule 2 (Code National) dedup into golden
# records. This is also what closes the "consolidated route doesn't reach OpenCR"
# gap. (In prod the EMRs also push to /CR/fhir with their facility client; this
# makes the consolidated server able to do it too.)
CR_URL = env("CR_URL", "http://openhim-core:5001/CR/fhir").rstrip("/")
CR_USER = env("CR_USER", "openshr")
CR_PASS = env("CR_PASS", "openshr")
ENROLL_MPI = env("ENROLL_MPI", "1").lower() in ("1", "true", "yes")

# --- global (non-patient-scoped) resources the EMR pipeline also ships ---
SYNC_GLOBALS = env("SYNC_GLOBALS", "1").lower() in ("1", "true", "yes")
GLOBAL_RESOURCES = [r.strip() for r in env(
    "GLOBAL_RESOURCES", "Practitioner,Location").split(",") if r.strip()]


def schema_to_fhir_base(schema):
    # openmrs -> isanteplus ; openmrs2 -> isanteplus2 ; etc.
    suffix = schema[len("openmrs"):] if schema.startswith("openmrs") else schema
    return EMR_FHIR_TEMPLATE.format(host="isanteplus" + suffix)


def _auth(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def http_get_json(url, user, pw):
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/fhir+json",
        "Authorization": _auth(user, pw),
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def collect_search_bundle(base, first_bundle):
    """Collect all resources from a fhir2 searchset, following `next` links."""
    out = []
    bundle = first_bundle
    pages = 0
    while bundle:
        for e in bundle.get("entry", []):
            r = e.get("resource")
            if r and r.get("resourceType") and r.get("resourceType") != "OperationOutcome":
                out.append(r)
        nxt = next((l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"), None)
        pages += 1
        if not nxt or pages >= 20:
            break
        bundle = http_get_json(nxt, EMR_USER, EMR_PASS)
    return out


def fetch_patient_fhir(base, uuid):
    """Pull the Patient + its clinical resources from the EMR fhir2 API."""
    resources = []
    # Patient (direct read)
    try:
        pat = http_get_json(f"{base}/Patient/{uuid}", EMR_USER, EMR_PASS)
        if pat.get("resourceType") == "Patient":
            resources.append(pat)
    except urllib.error.HTTPError as e:
        log.warning("Patient/%s fetch failed %s", uuid, e.code)
        return resources
    # patient-scoped clinical resources
    for rtype in PATIENT_RESOURCES:
        url = f"{base}/{rtype}?patient={uuid}&_count={PAGE_SIZE}"
        try:
            resources += collect_search_bundle(base, http_get_json(url, EMR_USER, EMR_PASS))
        except urllib.error.HTTPError as e:
            log.warning("%s?patient=%s failed %s (skipping)", rtype, uuid, e.code)
    return resources


def to_transaction(resources):
    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "resource": r,
                "request": {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"},
            }
            for r in resources if r.get("resourceType") and r.get("id")
        ],
    }


def post_to_shr(bundle):
    data = json.dumps(bundle).encode("utf-8")
    req = urllib.request.Request(SHR_URL, data=data, method="POST", headers={
        "Content-Type": "application/fhir+json",
        "Accept": "application/fhir+json",
        "Authorization": _auth(SHR_USER, SHR_PASS),
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status


def counts_by_type(resources):
    c = {}
    for r in resources:
        c[r["resourceType"]] = c.get(r["resourceType"], 0) + 1
    return c


def enroll_in_mpi(patient):
    """PUT the Patient (with all its identifiers, incl. biometric) to OpenCR,
    which enrolls it and runs matching/dedup into golden records."""
    url = f"{CR_URL}/Patient/{patient['id']}"
    data = json.dumps(patient).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PUT", headers={
        "Content-Type": "application/fhir+json",
        "Accept": "application/fhir+json",
        "Authorization": _auth(CR_USER, CR_PASS),
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def sync_globals(conn):
    """Push non-patient-scoped resources (Practitioner, Location, ...) from each
    facility's fhir2 to the SHR, matching the EMR pipeline's global coverage."""
    schemas = [r["_source_db"] for r in rows(conn, "SELECT DISTINCT _source_db FROM person")]
    for schema in schemas:
        base = schema_to_fhir_base(schema)
        for rtype in GLOBAL_RESOURCES:
            try:
                b = http_get_json(f"{base}/{rtype}?_count={PAGE_SIZE}", EMR_USER, EMR_PASS)
                res = collect_search_bundle(base, b)
            except urllib.error.HTTPError as e:
                log.warning("[%s] %s global fetch failed %s", schema, rtype, e.code)
                continue
            if not res:
                continue
            try:
                status = post_to_shr(to_transaction(res))
                log.info("[%s] global %s x%d -> SHR %s", schema, rtype, len(res), status)
            except urllib.error.HTTPError as e:
                log.error("[%s] global %s -> SHR FAILED %s", schema, rtype, e.code)


def publish_patient(conn, src, person_id):
    """Fetch one patient's FHIR from its EMR fhir2, POST to the SHR, and enroll
    in the MPI. Returns True on success (or nothing-to-do), False on failure.
    Shared by the poll-mode loop and the Kafka consumer."""
    person = rows(conn, "SELECT uuid FROM person WHERE _source_db=%s AND person_id=%s", (src, person_id))
    if not person:
        return True
    uuid = person[0]["uuid"]
    base = schema_to_fhir_base(src)
    try:
        resources = fetch_patient_fhir(base, uuid)
        if not resources:
            log.warning("[%s] Patient/%s: nothing fetched from %s", src, uuid, base)
            return True
        status = post_to_shr(to_transaction(resources))
        # fingerprint/identity -> MPI: enroll the Patient in OpenCR for dedup
        mpi = ""
        if ENROLL_MPI:
            patient_res = next((r for r in resources if r.get("resourceType") == "Patient"), None)
            if patient_res and patient_res.get("identifier"):  # OpenCR needs an identifier
                try:
                    mpi = f" | MPI {enroll_in_mpi(patient_res)}"
                except Exception as e:  # noqa: BLE001
                    mpi = f" | MPI FAILED: {e}"
        log.info("[%s] Patient/%s -> SHR %s | %s%s", src, uuid, status, counts_by_type(resources), mpi)
        return True
    except urllib.error.HTTPError as e:
        log.error("[%s] Patient/%s FAILED %s: %s", src, uuid, e.code,
                  e.read().decode("utf-8", "replace")[:400])
        return False
    except Exception as e:  # noqa: BLE001
        log.error("[%s] Patient/%s FAILED: %s", src, uuid, e)
        return False


def publish_once(conn):
    ensure_publish_state(conn)
    run_started = rows(conn, "SELECT NOW() AS now")[0]["now"]
    high_water = load_high_water(conn)
    keys = changed_patient_keys(conn, high_water)
    log.info("CDC-triggered fhir2 publish: %d changed patient(s) since %s", len(keys), high_water)
    ok = fail = 0
    for k in keys:
        if publish_patient(conn, k["_source_db"], k["person_id"]):
            ok += 1
        else:
            fail += 1
    if SYNC_GLOBALS:
        try:
            sync_globals(conn)
        except Exception as e:  # noqa: BLE001
            log.error("global sync failed: %s", e)
    if fail == 0:
        save_high_water(conn, run_started)
        log.info("publish run complete: %d ok, 0 failed; high-water -> %s", ok, run_started)
    else:
        log.info("publish run complete: %d ok, %d failed; high-water UNCHANGED (will retry)", ok, fail)
    return ok, fail


def main():
    while True:
        try:
            conn = connect_dst()
        except Exception as e:  # noqa: BLE001
            log.info("waiting for consolidated MySQL (%s)", e)
            time.sleep(3)
            continue
        try:
            publish_once(conn)
        finally:
            conn.close()
        if PUBLISH_INTERVAL <= 0:
            return
        log.info("sleeping %ds until next publish run", PUBLISH_INTERVAL)
        time.sleep(PUBLISH_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
