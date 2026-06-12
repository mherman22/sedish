"""
Consolidated-server CDC reader (prototype, production-schema edition).

A faithful stand-in for the production "Consolidé" ingest: it connects to the
iSantePlus MySQL as a replication client, reads the ROW-format binlog, and
upserts the clinical-core tables of every facility database into a single
"consolidated" MySQL **whose schema is the real production dump**
(`schema/consolidated_db_schema.sql`).

Unlike the earlier prototype (which invented its own tables keyed on
`(_source_db, <pk>)`), this writes into the production tables:

    source `person`  ->  `person_openmrs`   (+ mspp_code, date_updated)
    source `obs`     ->  `obs_openmrs`       ...

Each facility database maps to a site code (`mspp_code`) via SCHEMA_MSPP — how
the real consolidated_db distinguishes facilities (its tables are
PARTITION BY RANGE(year(date_created)) SUBPARTITION BY KEY(mspp_code), with the
PRIMARY KEY including mspp_code and date_created). `date_updated` is stamped on
every write — the column the downstream SQLMesh pipeline uses as its
incremental watermark.

Flow:
  1. Wait for source + consolidated MySQL to be reachable.
  2. If no saved binlog position: take an initial snapshot (SELECT * of each
     source table in each schema), then record the current master position.
  3. Stream binlog events from the saved position forever, applying
     insert/update -> upsert and delete -> delete, persisting the position
     after each event so restarts resume exactly where they left off.
"""
import os
import sys
import time
import logging

import pymysql
from pymysql.cursors import DictCursor
from pymysqlreplication import BinLogStreamReader
from pymysqlreplication.row_event import (
    WriteRowsEvent,
    UpdateRowsEvent,
    DeleteRowsEvent,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("cdc")


def env(name, default=None):
    return os.environ.get(name, default)


SRC = {
    "host": env("SRC_HOST", "mysql"),
    "port": int(env("SRC_PORT", "3306")),
    "user": env("SRC_USER", "consolidated"),
    "passwd": env("SRC_PASS", "consolidated"),
}
DST = {
    "host": env("DST_HOST", "consolidated-db"),
    "port": int(env("DST_PORT", "3306")),
    "user": env("DST_USER", "root"),
    "password": env("DST_PASS", "consolidated"),
    "db": env("DST_DB", "consolidated_db"),
}
SERVER_ID = int(env("SERVER_ID", "100001"))  # MUST differ from source server-id (223344)

# Production table naming: source `person` -> `person_openmrs`, etc.
TABLE_SUFFIX = env("TABLE_SUFFIX", "_openmrs")


def _parse_map(raw):
    out = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# Each facility database -> its site code (mspp_code), e.g. "openmrs=11106,openmrs2=22207"
SCHEMA_MSPP = _parse_map(env("SCHEMA_MSPP", "openmrs=11106,openmrs2=22207"))

# Kafka backbone (Pattern A): emit a patient-changed event per binlog row so the
# publisher can stream changes (resilient/replayable) instead of polling.
ENABLE_KAFKA = env("ENABLE_KAFKA", "").lower() in ("1", "true", "yes")
KAFKA_BROKERS = [b.strip() for b in env("KAFKA_BROKERS", "kafka:9092").split(",") if b.strip()]
KAFKA_TOPIC = env("KAFKA_TOPIC", "fhir.patient.changed")
# source table -> the column that identifies the owning patient (person_id)
PATIENT_KEY_COL = {
    "person": "person_id", "person_name": "person_id", "person_address": "person_id",
    "patient": "patient_id", "patient_identifier": "patient_id",
    "encounter": "patient_id", "obs": "person_id",
}
_producer = None
SCHEMAS = [s.strip() for s in env("SOURCE_SCHEMAS", "openmrs,openmrs2").split(",") if s.strip()]
TABLES = [t.strip() for t in env(
    "SOURCE_TABLES",
    "person,person_name,person_address,patient,patient_identifier,encounter,visit,obs",
).split(",") if t.strip()]

# caches so we introspect each table once
_src_cache = {}     # source table -> ordinal-ordered column names (binlog name remap)
_tgt_cache = {}     # target table -> (set(target columns), [target pk columns]) | None if absent


def connect_src(with_db=None):
    # exclude charset: BinLogStreamReader injects it into SRC, which would
    # otherwise collide with the explicit charset kwarg below
    cfg = {k: v for k, v in SRC.items() if k != "charset"}
    if with_db:
        cfg["database"] = with_db
    return pymysql.connect(charset="utf8mb4", autocommit=True, cursorclass=DictCursor, **cfg)


def connect_dst():
    return pymysql.connect(charset="utf8mb4", autocommit=True, cursorclass=DictCursor, **DST)


def target_of(table):
    return f"{table}{TABLE_SUFFIX}"


def mspp_for(schema):
    code = SCHEMA_MSPP.get(schema)
    if not code:
        raise RuntimeError(f"no mspp_code mapping for source schema {schema!r} "
                           f"(set SCHEMA_MSPP); known: {SCHEMA_MSPP}")
    return code


def get_producer():
    """Lazily create a best-effort Kafka producer (None if disabled/unavailable)."""
    global _producer
    if not ENABLE_KAFKA:
        return None
    if _producer is None:
        try:
            import json as _json
            from kafka import KafkaProducer
            _producer = KafkaProducer(
                bootstrap_servers=KAFKA_BROKERS,
                value_serializer=lambda v: _json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8"),
                acks="all", retries=5, linger_ms=50,
            )
            log.info("kafka producer connected to %s", KAFKA_BROKERS)
        except Exception as e:  # noqa: BLE001
            log.warning("kafka producer unavailable (%s); will retry", e)
            _producer = None
    return _producer


def emit_change(schema, table, values):
    """Emit a patient-changed event for the row's owning patient (best-effort)."""
    col = PATIENT_KEY_COL.get(table)
    if not col:
        return
    pid = values.get(col)
    if pid is None:
        return
    p = get_producer()
    if not p:
        return
    try:
        p.send(KAFKA_TOPIC, key=f"{schema}:{pid}",
               value={"source_db": schema, "mspp_code": mspp_for(schema), "person_id": int(pid)})
    except Exception as e:  # noqa: BLE001
        log.warning("kafka emit failed (%s.%s): %s", schema, table, e)


def wait_for(make_conn, label):
    while True:
        try:
            c = make_conn()
            c.close()
            log.info("connected to %s", label)
            return
        except Exception as e:  # noqa: BLE001
            log.info("waiting for %s (%s)", label, e)
            time.sleep(3)


def src_columns(src, table):
    """Ordinal-ordered source column names (used to remap positional binlog values)."""
    if table in _src_cache:
        return _src_cache[table]
    for schema in SCHEMAS:
        with src.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                (schema, table),
            )
            cols = [r["COLUMN_NAME"] for r in cur.fetchall()]
        if cols:
            _src_cache[table] = cols
            return cols
    _src_cache[table] = []
    return []


def target_meta(dst, target):
    """(set(columns), [pk columns]) for the production target table, or None if absent."""
    if target in _tgt_cache:
        return _tgt_cache[target]
    with dst.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
            (DST["db"], target),
        )
        cols = {r["COLUMN_NAME"] for r in cur.fetchall()}
        if not cols:
            _tgt_cache[target] = None
            return None
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' "
            "ORDER BY ORDINAL_POSITION",
            (DST["db"], target),
        )
        pk = [r["COLUMN_NAME"] for r in cur.fetchall()]
    _tgt_cache[target] = (cols, pk)
    return _tgt_cache[target]


def normalize_values(values, col_names):
    """pymysqlreplication sometimes returns positional keys (UNKNOWN_COL0, ...).
    Remap those to real column names by ordinal position (col_names is ordered)."""
    if not values:
        return {}
    if any(k in col_names for k in values):  # names already resolved
        return values
    out = {}
    for k, v in values.items():
        if isinstance(k, str) and k.startswith("UNKNOWN_COL"):
            try:
                idx = int(k[len("UNKNOWN_COL"):])
            except ValueError:
                continue
            if idx < len(col_names):
                out[col_names[idx]] = v
    return out


def upsert(dst, target, target_cols, mspp_code, values):
    """Upsert a source row into the production table, stamping mspp_code + date_updated."""
    cols = [c for c in values if c in target_cols and c not in ("mspp_code", "date_updated")]
    fields = ["`mspp_code`"] + [f"`{c}`" for c in cols] + ["`date_updated`"]
    placeholders = ", ".join(["%s"] + ["%s"] * len(cols) + ["NOW()"])
    updates = ", ".join([f"`{c}`=VALUES(`{c}`)" for c in cols] + ["`date_updated`=NOW()"])
    sql = (
        f"INSERT INTO `{target}` ({', '.join(fields)}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    params = [mspp_code] + [values[c] for c in cols]
    with dst.cursor() as cur:
        try:
            cur.execute(sql, params)
        except Exception:
            log.error("upsert failed target=%s cols=%d value_keys=%s",
                      target, len(cols), list(values.keys())[:6])
            raise


def delete(dst, target, target_pk, mspp_code, values):
    """Delete by the production primary key (which includes mspp_code)."""
    conds, params = [], []
    for c in target_pk:
        conds.append(f"`{c}`=%s")
        params.append(mspp_code if c == "mspp_code" else values.get(c))
    with dst.cursor() as cur:
        cur.execute(f"DELETE FROM `{target}` WHERE {' AND '.join(conds)}", params)


def ensure_state_table(dst):
    with dst.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS `_cdc_state` ("
            "id TINYINT NOT NULL PRIMARY KEY, "
            "log_file VARCHAR(255), log_pos BIGINT, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB"
        )


def load_state(dst):
    with dst.cursor() as cur:
        cur.execute("SELECT log_file, log_pos FROM `_cdc_state` WHERE id=1")
        row = cur.fetchone()
    if row and row["log_file"]:
        return row["log_file"], int(row["log_pos"])
    return None, None


def save_state(dst, log_file, log_pos):
    with dst.cursor() as cur:
        cur.execute(
            "INSERT INTO `_cdc_state` (id, log_file, log_pos) VALUES (1, %s, %s) "
            "ON DUPLICATE KEY UPDATE log_file=VALUES(log_file), log_pos=VALUES(log_pos)",
            (log_file, log_pos),
        )


def initial_snapshot(dst, src):
    with src.cursor() as cur:
        cur.execute("SHOW MASTER STATUS")
        master = cur.fetchone()
    log.info("initial snapshot starting; master at %s:%s", master["File"], master["Position"])
    total = 0
    for schema in SCHEMAS:
        mspp = mspp_for(schema)
        for table in TABLES:
            target = target_of(table)
            meta = target_meta(dst, target)
            if meta is None:
                log.warning("skip %s.%s: target %s not in %s", schema, table, target, DST["db"])
                continue
            tgt_cols, _ = meta
            sc = connect_src(with_db=schema)
            try:
                with sc.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                        (schema, table),
                    )
                    if not cur.fetchone():
                        continue
                    cur.execute(f"SELECT * FROM `{table}`")
                    rows = cur.fetchall()
                    for row in rows:
                        upsert(dst, target, tgt_cols, mspp, row)
                        emit_change(schema, table, row)
                    total += len(rows)
                    log.info("snapshot %s.%s -> %s (mspp=%s): %d rows",
                             schema, table, target, mspp, len(rows))
            finally:
                sc.close()
    save_state(dst, master["File"], master["Position"])
    log.info("initial snapshot complete: %d rows; resuming stream at %s:%s",
             total, master["File"], master["Position"])


def stream(dst):
    log_file, log_pos = load_state(dst)
    src = connect_src()  # used only for source-column introspection during streaming
    reader = BinLogStreamReader(
        connection_settings=dict(SRC),  # copy: the reader mutates this dict
        server_id=SERVER_ID,
        only_schemas=SCHEMAS,
        only_tables=TABLES,
        only_events=[WriteRowsEvent, UpdateRowsEvent, DeleteRowsEvent],
        resume_stream=True,
        log_file=log_file,
        log_pos=log_pos,
        blocking=True,
    )
    log.info("streaming binlog from %s:%s (schemas=%s)", log_file, log_pos, SCHEMAS)
    try:
        for event in reader:
            schema, table = event.schema, event.table
            target = target_of(table)
            meta = target_meta(dst, target)
            if meta is None:
                continue
            tgt_cols, tgt_pk = meta
            col_names = src_columns(src, table)
            mspp = mspp_for(schema)
            dst.ping(reconnect=True)
            for row in event.rows:
                if isinstance(event, WriteRowsEvent):
                    vals = normalize_values(row["values"], col_names)
                    upsert(dst, target, tgt_cols, mspp, vals)
                elif isinstance(event, UpdateRowsEvent):
                    vals = normalize_values(row["after_values"], col_names)
                    upsert(dst, target, tgt_cols, mspp, vals)
                else:  # DeleteRowsEvent
                    vals = normalize_values(row["values"], col_names)
                    delete(dst, target, tgt_pk, mspp, vals)
                emit_change(schema, table, vals)
            kind = type(event).__name__.replace("RowsEvent", "")
            log.info("%s %s.%s -> %s (%d row[s])", kind, schema, table, target, len(event.rows))
            save_state(dst, reader.log_file, reader.log_pos)
    finally:
        reader.close()
        src.close()


def main():
    wait_for(connect_src, "source MySQL")
    wait_for(connect_dst, "consolidated MySQL")
    dst = connect_dst()
    ensure_state_table(dst)
    src = connect_src()
    try:
        log_file, _ = load_state(dst)
        if log_file is None:
            initial_snapshot(dst, src)
    finally:
        src.close()
    # stream forever, reconnecting on transient errors
    while True:
        try:
            stream(dst)
        except Exception as e:  # noqa: BLE001
            log.exception("stream error, retrying in 5s: %s", e)
            time.sleep(5)
            dst.ping(reconnect=True)


if __name__ == "__main__":
    sys.exit(main())
