"""
Per-site incremental watermark. consolidated_db is READ-ONLY, so we persist the
high-water mark locally (a JSON file on a mounted volume), keyed by mspp_code.
The watermark is the max(date_created/date_changed) seen for that site; the next
run only pulls patients changed after it. Idempotent PUTs make re-processing safe.
"""
import json
import os

import db

STATE_PATH = os.environ.get("STATE_PATH", "/data/etl_state.json")


def _load():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, STATE_PATH)


def get_watermark(mspp_code):
    """ISO timestamp string the site was last synced to, or None for a full load."""
    return _load().get(mspp_code)


def site_max_timestamp(conn, mspp_code):
    """Max(date_created/date_changed) currently in the source for this site."""
    parts, params = [], []
    for table in db.PATIENT_SOURCE_TABLES:
        parts.append(
            f"SELECT GREATEST(COALESCE(MAX(date_changed),'1970-01-01'), "
            f"COALESCE(MAX(date_created),'1970-01-01')) AS t FROM {table} WHERE mspp_code=%s")
        params.append(mspp_code)
    sql = "SELECT MAX(t) AS mx FROM (" + " UNION ALL ".join(parts) + ") x"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        r = cur.fetchone()
    mx = r["mx"] if r else None
    return mx.isoformat() if hasattr(mx, "isoformat") else (str(mx) if mx else None)


def advance_watermark(conn, mspp_code):
    """Set the site watermark to its current max timestamp. (Capture-after; the
    small window vs the changed-ids query is covered by idempotent PUTs.)"""
    mx = site_max_timestamp(conn, mspp_code)
    if mx:
        d = _load()
        d[mspp_code] = mx
        _save(d)
