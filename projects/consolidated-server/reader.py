"""
Consolidated-server CDC reader (prototype).

A faithful stand-in for the production "Consolidé" ingest: it connects to the
iSantePlus MySQL as a replication client, reads the ROW-format binlog, and
upserts the clinical-core tables of every facility database into a single
"consolidated" MySQL.

Because multiple facility DBs (openmrs, openmrs2, ...) share the same primary
keys (patient_id=1 exists in each), every consolidated table is keyed on a
composite PK: (_source_db, <original primary key>).

Flow:
  1. Wait for source + consolidated MySQL to be reachable.
  2. If no saved binlog position: take an initial snapshot (SELECT * of each
     table in each schema), then record the current master position.
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
    "db": env("DST_DB", "consolidated"),
}
SERVER_ID = int(env("SERVER_ID", "100001"))  # MUST differ from source server-id (223344)
SCHEMAS = [s.strip() for s in env("SOURCE_SCHEMAS", "openmrs,openmrs2").split(",") if s.strip()]
TABLES = [t.strip() for t in env(
    "SOURCE_TABLES",
    "person,person_name,person_address,patient,patient_identifier,encounter,visit,obs",
).split(",") if t.strip()]

# cache of {table -> (column_names, pk_columns)} so we introspect each table once
_schema_cache = {}


def connect_src(with_db=None):
    # exclude charset: BinLogStreamReader injects it into SRC, which would
    # otherwise collide with the explicit charset kwarg below
    cfg = {k: v for k, v in SRC.items() if k != "charset"}
    if with_db:
        cfg["database"] = with_db
    return pymysql.connect(charset="utf8mb4", autocommit=True, cursorclass=DictCursor, **cfg)


def connect_dst():
    return pymysql.connect(charset="utf8mb4", autocommit=True, cursorclass=DictCursor, **DST)


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


def introspect(src, table):
    """Return (column_names, pk_columns) for the first schema that has the table."""
    if table in _schema_cache:
        return _schema_cache[table]
    for schema in SCHEMAS:
        with src.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                (schema, table),
            )
            cols = cur.fetchall()
            if not cols:
                continue
            cur.execute(
                "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' "
                "ORDER BY ORDINAL_POSITION",
                (schema, table),
            )
            pk = [r["COLUMN_NAME"] for r in cur.fetchall()]
            result = ([c["COLUMN_NAME"] for c in cols], pk, cols)
            _schema_cache[table] = result
            return result
    raise RuntimeError(f"table {table} not found in any of {SCHEMAS}")


def ensure_table(dst, src, table):
    """Create the consolidated table mirroring source columns + composite PK."""
    col_names, pk, col_meta = introspect(src, table)
    pk_set = set(pk)
    defs = ["`_source_db` VARCHAR(64) NOT NULL"]
    for c in col_meta:
        # PK columns must stay NOT NULL; all others made nullable to avoid
        # strict-mode/default friction during upserts
        nullability = "NOT NULL" if c["COLUMN_NAME"] in pk_set else "NULL"
        defs.append(f"`{c['COLUMN_NAME']}` {c['COLUMN_TYPE']} {nullability}")
    defs.append("`_synced_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
    pk_cols = ", ".join(["`_source_db`"] + [f"`{c}`" for c in pk])
    ddl = (
        f"CREATE TABLE IF NOT EXISTS `{table}` ({', '.join(defs)}, "
        f"PRIMARY KEY ({pk_cols})) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    with dst.cursor() as cur:
        cur.execute(ddl)
    return col_names, pk


def normalize_values(values, col_names):
    """pymysqlreplication sometimes can't resolve column names and returns
    positional keys (UNKNOWN_COL0, UNKNOWN_COL1, ...). Remap those to the real
    column names by ordinal position (col_names is ORDINAL_POSITION-ordered)."""
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


def upsert(dst, table, col_names, source_db, values):
    cols = [c for c in col_names if c in values]
    fields = ["`_source_db`"] + [f"`{c}`" for c in cols]
    placeholders = ", ".join(["%s"] * len(fields))
    updates = ", ".join([f"`{c}`=VALUES(`{c}`)" for c in cols])
    sql = (
        f"INSERT INTO `{table}` ({', '.join(fields)}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    params = [source_db] + [values[c] for c in cols]
    with dst.cursor() as cur:
        try:
            cur.execute(sql, params)
        except Exception:
            log.error("upsert failed table=%s cols=%d sql=%r value_keys=%s col_names=%s",
                      table, len(cols), sql, list(values.keys())[:6], col_names[:6])
            raise


def delete(dst, table, pk, source_db, values):
    where = " AND ".join(["`_source_db`=%s"] + [f"`{c}`=%s" for c in pk])
    params = [source_db] + [values[c] for c in pk]
    with dst.cursor() as cur:
        cur.execute(f"DELETE FROM `{table}` WHERE {where}", params)


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
        for table in TABLES:
            try:
                col_names, pk = ensure_table(dst, src, table)
            except RuntimeError as e:
                log.warning("skip %s: %s", table, e)
                continue
            sc = connect_src(with_db=schema)
            try:
                with sc.cursor() as cur:
                    # confirm table exists in THIS schema before selecting
                    cur.execute(
                        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                        (schema, table),
                    )
                    if not cur.fetchone():
                        continue
                    cur.execute(f"SELECT * FROM `{table}`")
                    rows = cur.fetchall()
                    for row in rows:
                        upsert(dst, table, col_names, schema, row)
                    total += len(rows)
                    log.info("snapshot %s.%s: %d rows", schema, table, len(rows))
            finally:
                sc.close()
    save_state(dst, master["File"], master["Position"])
    log.info("initial snapshot complete: %d rows; resuming stream at %s:%s",
             total, master["File"], master["Position"])


def stream(dst):
    log_file, log_pos = load_state(dst)
    src = connect_src()  # used only for introspection during streaming
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
            schema = event.schema
            table = event.table
            col_names, pk = ensure_table(dst, src, table)
            dst.ping(reconnect=True)
            for row in event.rows:
                if isinstance(event, WriteRowsEvent):
                    upsert(dst, table, col_names, schema, normalize_values(row["values"], col_names))
                elif isinstance(event, UpdateRowsEvent):
                    upsert(dst, table, col_names, schema, normalize_values(row["after_values"], col_names))
                elif isinstance(event, DeleteRowsEvent):
                    delete(dst, table, pk, schema, normalize_values(row["values"], col_names))
            kind = type(event).__name__.replace("RowsEvent", "")
            log.info("%s %s.%s (%d row[s])", kind, schema, table, len(event.rows))
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
