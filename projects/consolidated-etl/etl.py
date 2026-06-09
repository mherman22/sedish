"""
SEDISH consolidated -> FHIR ETL (DIGI side of the CHARESS handoff).

For each changed patient (keyed by mspp_code, patient_id):
  1. resolve the patient from consolidated_db (§8.1),
  2. build FHIR (Patient + identifiers + national_id; Encounters; Observations),
  3. load IDENTITY -> OpenCR (/CR/fhir) and CLINICAL -> SHR (/SHR/fhir), via OpenHIM.

Batched by mspp_code (partition pruning, §8.3), idempotent (PUT by uuid), and
incremental (local watermark per site — coverage grows as dedup runs, §3/§12).
Production load waits for the dedup run to finish; dev runs against the schema.
"""
import base64
import json
import os
import sys
import time
import logging
import urllib.request
import urllib.error

import db
import mapping
import state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("etl")


def env(n, d=None):
    return os.environ.get(n, d)


# OpenHIM destinations
OPENCR_URL = env("OPENCR_URL", "http://openhim-core:5001/CR/fhir").rstrip("/")
OPENCR_USER = env("OPENCR_USER", "openshr")
OPENCR_PASS = env("OPENCR_PASS", "openshr")
SHR_URL = env("SHR_URL", "http://openhim-core:5001/SHR/fhir").rstrip("/")
SHR_USER = env("SHR_USER", "shr-pipeline")
SHR_PASS = env("SHR_PASS", "instant101")
# FHIR system URIs — CHARESS to provide canonical namespaces (§9, §11)
MRN_SYSTEM_BASE = env("MRN_SYSTEM_BASE", "http://sedish.sedishtest.live")
NATIONAL_ID_SYSTEM = env("NATIONAL_ID_SYSTEM", "http://sedish.sedishtest.live/national-fp-id")
# scoping / cadence
SITE_FILTER = [s.strip() for s in env("SITE_FILTER", "").split(",") if s.strip()]  # empty = all sites
RUN_INTERVAL = int(env("RUN_INTERVAL", "0"))  # 0 = run once and exit
DRY_RUN = env("DRY_RUN", "").lower() in ("1", "true", "yes")  # build but don't push


def _auth(u, p):
    return "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()


def post(url, user, pw, payload):
    if DRY_RUN:
        return 0
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/fhir+json", "Accept": "application/fhir+json",
        "Authorization": _auth(user, pw)})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status


def put(url, user, pw, resource):
    if DRY_RUN:
        return 0
    data = json.dumps(resource).encode("utf-8")
    req = urllib.request.Request(f"{url}/{resource['resourceType']}/{resource['id']}",
                                 data=data, method="PUT", headers={
        "Content-Type": "application/fhir+json", "Accept": "application/fhir+json",
        "Authorization": _auth(user, pw)})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def publish_patient(conn, resolver, mspp_code, patient_id):
    resolved = db.resolve_patient(conn, mspp_code, patient_id)
    if not resolved:
        return False
    patient = mapping.build_patient(
        resolved["person"], resolved["names"], resolved["addresses"],
        resolved["identifiers"], resolved["mapping"],
        mrn_system_base=MRN_SYSTEM_BASE, national_id_system=NATIONAL_ID_SYSTEM)

    # IDENTITY -> OpenCR (all patients; national_id overlay handled in build_patient)
    cr = put(OPENCR_URL, OPENCR_USER, OPENCR_PASS, patient)

    # CLINICAL -> SHR (Patient + its Encounters + Observations, one transaction)
    encs = db.get_encounters(conn, mspp_code, patient_id)
    enc_uuid_by_id = {e["encounter_id"]: e["uuid"] for e in encs}
    resources = [patient] + [mapping.build_encounter(e, patient["id"]) for e in encs]
    for o in db.get_observations(conn, mspp_code, patient_id):
        resources.append(mapping.build_observation(
            o, patient["id"], enc_uuid_by_id.get(o.get("encounter_id")), resolver.resolve))
    shr = post(SHR_URL, SHR_USER, SHR_PASS, mapping.transaction_bundle(resources))

    log.info("[%s/%s] Patient/%s -> OpenCR %s, SHR %s (%d enc, %d obs, nid=%s)",
             mspp_code, patient_id, patient["id"], cr, shr,
             len(encs), len(resources) - 1 - len(encs),
             (resolved["mapping"] or {}).get("national_id"))
    return True


def run_once(conn):
    resolver = db.ConceptResolver(conn)
    sites = SITE_FILTER or db.list_sites(conn)
    log.info("ETL run over %d site(s)%s", len(sites), " [DRY_RUN]" if DRY_RUN else "")
    total_ok = total_fail = 0
    for mspp_code in sites:
        since = state.get_watermark(mspp_code)
        ids = db.changed_patient_ids(conn, mspp_code, since)
        if not ids:
            continue
        log.info("[%s] %d changed patient(s) since %s", mspp_code, len(ids), since)
        ok = fail = 0
        for pid in ids:
            try:
                if publish_patient(conn, resolver, mspp_code, pid):
                    ok += 1
            except urllib.error.HTTPError as e:
                fail += 1
                log.error("[%s/%s] FAILED %s: %s", mspp_code, pid, e.code,
                          e.read().decode("utf-8", "replace")[:300])
            except Exception as e:  # noqa: BLE001
                fail += 1
                log.error("[%s/%s] FAILED: %s", mspp_code, pid, e)
        # advance the site watermark only on a clean batch (else retry next run)
        if fail == 0:
            state.advance_watermark(conn, mspp_code)
        total_ok += ok
        total_fail += fail
        log.info("[%s] done: %d ok, %d failed", mspp_code, ok, fail)
    log.info("ETL run complete: %d ok, %d failed", total_ok, total_fail)


def main():
    while True:
        try:
            conn = db.connect()
        except Exception as e:  # noqa: BLE001
            log.info("waiting for consolidated_db (%s)", e)
            time.sleep(5)
            continue
        try:
            run_once(conn)
        finally:
            conn.close()
        if RUN_INTERVAL <= 0:
            return
        log.info("sleeping %ds until next run", RUN_INTERVAL)
        time.sleep(RUN_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
