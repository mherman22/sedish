"""
Parity-diff harness: consolidated route vs the EMR fhir2 gold standard.

For every consolidated patient it asks two questions:
  1. What does the owning EMR's fhir2 API hold for this patient? (= the
     source-of-truth FHIR — the gold standard.)
  2. Is each of those resources present in the SHR? (= what our consolidated
     route actually delivered.)

It reports, per resource type, matched / missing-in-SHR counts. A clean run
(zero missing) confirms the consolidated route populates the SHR with the same
resources as the source of truth.

Resources are matched by id (OpenMRS uuid, preserved end-to-end), so the SHR
mediator's golden-record subject rewrite doesn't affect the comparison.

Run inside the publisher container (it has the consolidated-db, EMR fhir2 and
SHR all reachable):  docker exec <publisher> python -u parity.py
"""
import sys
import logging
import urllib.request
import urllib.error

from publisher import connect_dst, rows
import publisher_fhir2 as P

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("parity")


def shr_has(rtype, rid):
    """True if ResourceType/id exists in the SHR (via the OpenHIM passthrough)."""
    url = f"{P.SHR_URL}/{rtype}/{rid}"
    req = urllib.request.Request(url, method="GET", headers={
        "Accept": "application/fhir+json",
        "Authorization": P._auth(P.SHR_USER, P.SHR_PASS),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return False
        raise


def gold_inventory(base, uuid):
    """type -> [ids] that the EMR fhir2 holds for this patient."""
    inv = {}
    try:
        pat = P.http_get_json(f"{base}/Patient/{uuid}", P.EMR_USER, P.EMR_PASS)
        if pat.get("resourceType") == "Patient":
            inv["Patient"] = [uuid]
    except urllib.error.HTTPError:
        return inv
    for rtype in P.PATIENT_RESOURCES:
        try:
            b = P.http_get_json(f"{base}/{rtype}?patient={uuid}&_count={P.PAGE_SIZE}",
                                P.EMR_USER, P.EMR_PASS)
            inv[rtype] = [r["id"] for r in P.collect_search_bundle(base, b) if r.get("id")]
        except urllib.error.HTTPError as e:
            log.warning("%s?patient=%s fhir2 error %s", rtype, uuid, e.code)
    return inv


def main():
    conn = connect_dst()
    patients = rows(conn,
        "SELECT p._source_db, p.person_id, p.uuid FROM person p JOIN patient pt "
        "ON pt._source_db=p._source_db AND pt.patient_id=p.person_id")
    log.info("parity check over %d patient(s)", len(patients))

    totals = {}   # type -> [gold, present, missing]
    for pr in patients:
        src, uuid = pr["_source_db"], pr["uuid"]
        base = P.schema_to_fhir_base(src)
        inv = gold_inventory(base, uuid)
        line = []
        for rtype, ids in inv.items():
            present = sum(1 for rid in ids if shr_has(rtype, rid))
            missing = len(ids) - present
            t = totals.setdefault(rtype, [0, 0, 0])
            t[0] += len(ids); t[1] += present; t[2] += missing
            line.append(f"{rtype}={present}/{len(ids)}")
        log.info("[%s] Patient/%s  %s", src, uuid, "  ".join(line) or "(no fhir2 resources)")

    print("\n==================== PARITY SUMMARY ====================")
    print(f"{'ResourceType':<22}{'gold(fhir2)':>12}{'in SHR':>10}{'MISSING':>10}")
    grand_missing = 0
    for rtype, (g, p, m) in sorted(totals.items()):
        grand_missing += m
        print(f"{rtype:<22}{g:>12}{p:>10}{m:>10}")
    print("-" * 54)
    verdict = "PARITY ✔ (SHR has everything fhir2 has)" if grand_missing == 0 \
        else f"GAP: {grand_missing} resource(s) missing from SHR"
    print(verdict)
    print("Note: Practitioner/Location/Group are global (not patient-scoped) and")
    print("are intentionally out of this patient-centric comparison.")
    conn.close()
    return 0 if grand_missing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
