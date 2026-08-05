#!/usr/bin/env python3
"""Bulk tooling for the SEDISH Client Registry.

Three jobs, deliberately separate because they use different write paths:

  reindex   FHIR store -> Elasticsearch, in bulk. Bypasses OpenCR entirely, so it never
            triggers matching. Use after changing PatientRelationship.json (which defines the
            index) or when the index is stale. This is also the only way to rebuild the index
            at all: OpenCR's own startup sync (cacheFHIR.fhir2ES) stops after ONE page of 20
            records, so a wiped index silently comes back with 20 of N records and every rule
            then finds no candidates.

  load      CSV -> FHIR -> OpenCR's /fhir endpoint. Goes THROUGH matching, which is the point:
            it is how you exercise the decision rules with a corpus.

  verify    Reads back what the goldens look like and scores a rule-test corpus: rows sharing a
            case id (1A/1B) are expected to land on one golden when Match Type is "Auto".

Streams throughout, so 100k rows is a memory non-issue. Only stdlib.
"""

import argparse
import base64
import collections
import concurrent.futures as futures
import csv
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 60


# ---------------------------------------------------------------------------- http

def _req(method, url, body=None, headers=None, timeout=DEFAULT_TIMEOUT):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:400].decode("utf-8", "replace")}


def _auth_header(user, password):
    if not user:
        return {}
    token = base64.b64encode(f"{user}:{password or ''}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ------------------------------------------------------------- tiny fhirpath subset

WHERE = re.compile(r"^(\w+)\.where\((\w+)='([^']*)'\)(.*)$")


def fhirpath(resource, path):
    """Evaluate the small fhirpath subset PatientRelationship.json actually uses.

    Supports: plain dotted paths, `.where(key='value')` filters, and `.last()`. That is the
    whole grammar OpenCR permits anyway ("simple paths or .where() only"), so a full evaluator
    would be dead weight.
    """
    node = resource
    rest = path
    while rest:
        rest = rest.lstrip(".")
        m = WHERE.match(rest)
        if m:
            field, key, val, rest = m.groups()
            items = node.get(field) if isinstance(node, dict) else None
            items = items if isinstance(items, list) else ([items] if items else [])
            node = [i for i in items if isinstance(i, dict) and i.get(key) == val]
            continue
        if rest.startswith("last()"):
            node = node[-1] if isinstance(node, list) and node else node
            rest = rest[len("last()"):]
            continue
        seg, _, rest = rest.partition(".")
        if not seg:
            break
        if isinstance(node, list):
            out = []
            for i in node:
                v = i.get(seg) if isinstance(i, dict) else None
                out.extend(v if isinstance(v, list) else ([v] if v is not None else []))
            node = out
        elif isinstance(node, dict):
            node = node.get(seg)
        else:
            return ""
    if isinstance(node, list):
        node = node[0] if node else ""
    return "" if node is None else str(node)


def index_elements(relationship_json):
    """[(es_field, fhirpath)] from PatientRelationship.json, so this tool and the index cannot
    drift apart: the file is the single definition of what is indexed."""
    d = json.load(open(relationship_json, encoding="utf-8"))
    out = []
    for e in d["extension"][0]["extension"]:
        if not e.get("url", "").endswith("iHRISReportElement"):
            continue
        label = next(x["valueString"] for x in e["extension"] if x["url"] == "label")
        name = next(x["valueString"] for x in e["extension"] if x["url"] == "name")
        out.append((label, name))
    return out


# ------------------------------------------------------------------------ fhir paging

def iter_patients(fhir_base, page_size=200, elements=None, auth=None):
    """Yield Patient resources, following `next` links; falls back to offset paging when the
    server emits a `next` we cannot reach (HAPI behind a gateway rewrites it)."""
    params = {"_count": str(page_size)}
    if elements:
        params["_elements"] = ",".join(elements)
    url = f"{fhir_base}/Patient?{urllib.parse.urlencode(params)}"
    offset, seen = 0, 0
    while url:
        status, bundle = _req("GET", url, headers=auth)
        if status >= 300 or not bundle:
            raise SystemExit(f"FHIR read failed ({status}): {str(bundle)[:200]}")
        entries = bundle.get("entry") or []
        for e in entries:
            if e.get("resource", {}).get("resourceType") == "Patient":
                seen += 1
                yield e["resource"]
        if len(entries) < page_size:
            return
        offset += page_size
        params["_getpagesoffset"] = str(offset)
        url = f"{fhir_base}/Patient?{urllib.parse.urlencode(params)}"


# --------------------------------------------------------------------------- reindex

def cmd_reindex(a):
    elements = index_elements(a.relationship)
    fields = [f for f, _ in elements]
    print(f"indexing fields: {', '.join(fields)}")
    auth = _auth_header(a.fhir_user, a.fhir_pass)

    batch, n, updated, skipped = [], 0, 0, 0
    def flush():
        nonlocal batch, updated
        if not batch:
            return
        body = ("\n".join(batch) + "\n").encode()
        status, res = _req("POST", f"{a.es}/_bulk", body=body,
                           headers={"Content-Type": "application/x-ndjson"})
        if status >= 300:
            raise SystemExit(f"bulk failed ({status}): {str(res)[:300]}")
        for item in (res or {}).get("items", []):
            op = item.get("index") or item.get("update") or {}
            if op.get("status", 500) < 300:
                updated += 1
            else:
                print(f"  ! {op.get('_id')}: {str(op.get('error'))[:120]}", file=sys.stderr)
        batch = []

    for res in iter_patients(a.fhir, page_size=a.page_size, auth=auth):
        n += 1
        doc = {}
        for field, path in elements:
            v = fhirpath(res, path)
            if v:
                doc[field] = v
        # A golden record carries no demographics; indexing it would put an empty document in
        # the candidate pool for every query.
        if not doc.get("given") and not doc.get("family"):
            skipped += 1
        else:
            doc["patients"] = f"Patient/{res['id']}"
            batch.append(json.dumps({"update": {"_id": res["id"], "_index": a.index}}))
            batch.append(json.dumps({"doc": doc, "doc_as_upsert": True}))
        if len(batch) >= a.batch * 2:
            flush()
            print(f"  … {n} read, {updated} indexed", end="\r", flush=True)
    flush()
    print(f"\nread {n} patients, indexed {updated}, skipped {skipped} (no name — golden shells)")


# ------------------------------------------------------------------------------ load

def row_to_patient(row, mapping, source_system):
    """CSV row -> FHIR Patient. Only mapped, non-empty columns are emitted."""
    def g(key):
        col = mapping.get(key)
        return (row.get(col) or "").strip() if col else ""

    given = " ".join(x for x in (g("first_name"), g("second_name")) if x).strip()
    ident = []
    for key, system in (("up_id", "up-id"), ("art_id", "art-id"), ("national_id", "national-id")):
        v = g(key)
        if v:
            ident.append({"use": "official", "system": f"{source_system}/{system}", "value": v})
    if not ident:
        return None, "no identifier"

    p = {
        "resourceType": "Patient",
        "identifier": ident,
        "name": [{"use": "official", "given": [given] if given else [],
                  "family": g("last_name"), "text": f"{given} {g('last_name')}".strip()}],
    }
    if g("gender"):
        p["gender"] = g("gender").lower()
    if g("date_of_birth"):
        p["birthDate"] = g("date_of_birth")
    tel = [{"system": "phone", "value": re.sub(r"\s+", "", v)}
           for v in (g("cell_number"), g("tel_number")) if v]
    if tel:
        p["telecom"] = tel
    if g("mother"):
        p["contact"] = [{"name": {"text": g("mother")},
                         "relationship": [{"coding": [{
                             "system": "http://terminology.hl7.org/CodeSystem/v3-RoleCode",
                             "code": "MTH"}]}]}]
    return p, None


def cmd_load(a):
    mapping = json.loads(a.mapping) if a.mapping else {
        "first_name": "first_name", "second_name": "second_name", "last_name": "last_name",
        "gender": "gender", "date_of_birth": "date_of_birth", "cell_number": "cell_number",
        "tel_number": "tel_number", "up_id": "up_id", "art_id": "art_id",
        "national_id": "National ID", "case": "Comment", "expect": "Match Type",
        "facility": "facilty",
    }
    headers = {"Content-Type": "application/json", "x-openhim-clientid": a.client_id}
    headers.update(_auth_header(a.cr_user, a.cr_pass))

    def push(idx_row):
        idx, row = idx_row
        p, err = row_to_patient(row, mapping, a.source_system)
        if err:
            return ("skip", idx, err)
        rid = f"{a.id_prefix}-{idx:06d}"
        p["id"] = rid
        status, res = _req("PUT", f"{a.cr}/fhir/Patient/{rid}", body=p, headers=headers)
        return ("ok" if status < 300 else "fail", idx, status)

    counts = collections.Counter()
    with open(a.csv, newline="", encoding="utf-8-sig") as fh:
        rows = enumerate(csv.DictReader(fh))
        if a.limit:
            rows = (r for i, r in enumerate(rows) if i < a.limit)
        with futures.ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            for kind, idx, info in ex.map(push, rows):
                counts[kind] += 1
                if kind == "fail":
                    print(f"  ! row {idx}: HTTP {info}", file=sys.stderr)
                if sum(counts.values()) % 50 == 0:
                    print(f"  … {sum(counts.values())} pushed", end="\r", flush=True)
    print(f"\nloaded: {dict(counts)}")
    print(f"records are id-prefixed '{a.id_prefix}-' — use `verify` next, and that prefix to clean up")


# ---------------------------------------------------------------------------- verify

CASE_ID = re.compile(r"^\s*(\d+)\s*([A-Za-z])\b")


def cmd_verify(a):
    """Score a rule-test corpus: rows whose Comment starts with the same case number (1A/1B)
    are one expected cluster; Match Type says whether they should auto-link."""
    auth = _auth_header(a.fhir_user, a.fhir_pass)
    golden_of = {}
    for res in iter_patients(a.fhir, page_size=a.page_size, elements=["link"], auth=auth):
        if not res["id"].startswith(a.id_prefix + "-"):
            continue
        links = [l.get("other", {}).get("reference") for l in res.get("link", [])]
        golden_of[res["id"]] = links[0] if links else None

    cases = collections.defaultdict(lambda: {"expect": "", "ids": [], "label": ""})
    with open(a.csv, newline="", encoding="utf-8-sig") as fh:
        for idx, row in enumerate(csv.DictReader(fh)):
            comment = (row.get("Comment") or "").strip()
            m = CASE_ID.match(comment)
            if not m:
                continue
            c = cases[m.group(1)]
            c["expect"] = (row.get("Match Type") or "").strip().lower()
            c["label"] = c["label"] or comment
            c["ids"].append(f"{a.id_prefix}-{idx:06d}")

    passed, failed, partial = [], [], []
    for case, c in sorted(cases.items(), key=lambda kv: int(kv[0])):
        goldens = {golden_of.get(i) for i in c["ids"] if i in golden_of}
        present = [i for i in c["ids"] if i in golden_of]
        if len(present) < 2:
            partial.append((case, c, "not all records loaded"))
            continue
        linked = len(goldens) == 1 and None not in goldens
        want_auto = c["expect"].startswith("auto")
        (passed if linked == want_auto else failed).append((case, c, linked))

    print(f"cases: {len(cases)}   pass: {len(passed)}   fail: {len(failed)}   skipped: {len(partial)}")
    for case, c, linked in failed:
        print(f"  FAIL case {case}: expected {c['expect'] or 'no-match'}, "
              f"{'linked' if linked else 'not linked'} — {c['label'][:80]}")
    for case, c, why in partial:
        print(f"  SKIP case {case}: {why}")
    return 1 if failed else 0


# ------------------------------------------------------------------------------ cli

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("reindex", help="rebuild the ES index from the FHIR store (no matching)")
    r.add_argument("--fhir", default="http://opencr-fhir:8080/fhir")
    r.add_argument("--es", default="http://localhost:9200")
    r.add_argument("--index", default="patients")
    r.add_argument("--relationship", default="/src/resources/Relationships/PatientRelationship.json")
    r.add_argument("--page-size", type=int, default=200)
    r.add_argument("--batch", type=int, default=500, help="docs per _bulk request")
    r.add_argument("--fhir-user", default=""); r.add_argument("--fhir-pass", default="")
    r.set_defaults(func=cmd_reindex)

    l = sub.add_parser("load", help="push a CSV through OpenCR so matching runs")
    l.add_argument("csv")
    l.add_argument("--cr", default="http://opencr:3000")
    l.add_argument("--client-id", default="openmrs")
    l.add_argument("--id-prefix", default="bulk")
    l.add_argument("--source-system", default="http://sedish-haiti.org/fhir/bulk")
    l.add_argument("--mapping", default="", help="JSON overriding the default column mapping")
    l.add_argument("--concurrency", type=int, default=8)
    l.add_argument("--limit", type=int, default=0)
    l.add_argument("--cr-user", default=""); l.add_argument("--cr-pass", default="")
    l.set_defaults(func=cmd_load)

    v = sub.add_parser("verify", help="score a rule-test corpus against the goldens formed")
    v.add_argument("csv")
    v.add_argument("--fhir", default="http://opencr-fhir:8080/fhir")
    v.add_argument("--id-prefix", default="bulk")
    v.add_argument("--page-size", type=int, default=200)
    v.add_argument("--fhir-user", default=""); v.add_argument("--fhir-pass", default="")
    v.set_defaults(func=cmd_verify)

    a = ap.parse_args()
    sys.exit(a.func(a) or 0)


if __name__ == "__main__":
    main()
