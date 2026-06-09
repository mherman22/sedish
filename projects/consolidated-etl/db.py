"""
Read-only access to the CHARESS consolidated_db, following the spec's joins.

Everything is keyed on the composite (mspp_code, patient_id); person_id = patient_id
(§6.1, §8.1). voided rows are filtered out; preferred=1 is preferred for name/MRN.
The source is READ-ONLY, so incremental watermarks are kept locally (state.py),
never written back to consolidated_db.
"""
import os
import pymysql
from pymysql.cursors import DictCursor


def env(n, d=None):
    return os.environ.get(n, d)


SRC = {
    "host": env("SRC_HOST", "127.0.0.1"),
    "port": int(env("SRC_PORT", "3310")),
    "user": env("SRC_USER", "readonly"),
    "password": env("SRC_PASS", ""),
    "database": env("SRC_DB", "consolidated_db"),
}

# Tables whose changes mean a patient must be (re)published. Maps table -> the
# column that identifies the owning patient (= patient_id / person_id).
PATIENT_SOURCE_TABLES = {
    "patient_openmrs": "patient_id",
    "person_openmrs": "person_id",
    "person_name_openmrs": "person_id",
    "person_address_openmrs": "person_id",
    "patient_identifier_openmrs": "patient_id",
    "encounter_openmrs": "patient_id",
    "obs_openmrs": "person_id",
}


def connect():
    return pymysql.connect(charset="utf8mb3", cursorclass=DictCursor, autocommit=True, **SRC)


def list_sites(conn):
    """All mspp_codes present, for batch-by-site processing (§8.3)."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT mspp_code FROM patient_openmrs ORDER BY mspp_code")
        return [r["mspp_code"] for r in cur.fetchall()]


def changed_patient_ids(conn, mspp_code, since):
    """Distinct patient_ids at a site changed since `since` (a datetime or None
    for full load). Unions the patient-contributing tables on date_created/date_changed."""
    parts, params = [], []
    for table, col in PATIENT_SOURCE_TABLES.items():
        cond = ""
        if since is not None:
            cond = " AND (date_created > %s OR date_changed > %s)"
        parts.append(f"SELECT DISTINCT {col} AS pid FROM {table} WHERE mspp_code=%s{cond}")
        params.append(mspp_code)
        if since is not None:
            params += [since, since]
    sql = " UNION ".join(parts)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [r["pid"] for r in cur.fetchall() if r["pid"] is not None]


def _rows(conn, sql, params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def resolve_patient(conn, mspp_code, patient_id):
    """The canonical resolved-patient join (§8.1). Returns the parts needed to
    build a FHIR Patient, or None if the patient/person row is missing."""
    person = _rows(conn,
        "SELECT * FROM person_openmrs WHERE mspp_code=%s AND person_id=%s "
        "AND (voided=0 OR voided IS NULL) LIMIT 1", (mspp_code, patient_id))
    if not person:
        return None
    names = _rows(conn,
        "SELECT * FROM person_name_openmrs WHERE mspp_code=%s AND person_id=%s "
        "AND (voided=0 OR voided IS NULL) ORDER BY preferred DESC, person_name_id ASC",
        (mspp_code, patient_id))
    addrs = _rows(conn,
        "SELECT * FROM person_address_openmrs WHERE mspp_code=%s AND person_id=%s "
        "AND (voided=0 OR voided IS NULL) ORDER BY preferred DESC", (mspp_code, patient_id))
    idents = _rows(conn,
        "SELECT * FROM patient_identifier_openmrs WHERE mspp_code=%s AND patient_id=%s "
        "AND (voided=0 OR voided IS NULL) ORDER BY preferred DESC", (mspp_code, patient_id))
    mapping = _rows(conn,
        "SELECT * FROM national_fingerprint_mapping WHERE mspp_code=%s AND patient_id=%s LIMIT 1",
        (mspp_code, patient_id))
    return {"person": person[0], "names": names, "addresses": addrs,
            "identifiers": idents, "mapping": mapping[0] if mapping else None}


def get_encounters(conn, mspp_code, patient_id):
    return _rows(conn,
        "SELECT * FROM encounter_openmrs WHERE mspp_code=%s AND patient_id=%s "
        "AND (voided=0 OR voided IS NULL)", (mspp_code, patient_id))


def get_observations(conn, mspp_code, patient_id):
    return _rows(conn,
        "SELECT * FROM obs_openmrs WHERE mspp_code=%s AND person_id=%s "
        "AND (voided=0 OR voided IS NULL)", (mspp_code, patient_id))


class ConceptResolver:
    """concept_id -> {display, codings[]}. Uses concept_name for the display label
    (§10.1) and, IF the CIEL reference-map tables are present, real codings. The
    concept dictionary is shared across sites (not partitioned), so no mspp_code.
    Degrades gracefully (label-only) when the reference tables are absent."""

    def __init__(self, conn):
        self.conn = conn
        self.cache = {}
        self._has_refmaps = None

    def _refmaps_available(self):
        if self._has_refmaps is None:
            try:
                with self.conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM concept_reference_map LIMIT 1")
                    cur.fetchall()
                self._has_refmaps = True
            except Exception:
                self._has_refmaps = False
        return self._has_refmaps

    def resolve(self, concept_id):
        if concept_id is None:
            return None
        if concept_id in self.cache:
            return self.cache[concept_id]
        display = None
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT name FROM concept_name WHERE concept_id=%s "
                    "AND (voided=0 OR voided IS NULL) "
                    "ORDER BY (locale_preferred=1) DESC, (concept_name_type='FULLY_SPECIFIED') DESC "
                    "LIMIT 1", (concept_id,))
                r = cur.fetchone()
                if r:
                    display = r["name"]
        except Exception:
            pass
        codings = []
        if self._refmaps_available():
            try:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT crs.name AS source, crt.code AS code "
                        "FROM concept_reference_map crm "
                        "JOIN concept_reference_term crt ON crm.concept_reference_term_id=crt.concept_reference_term_id "
                        "JOIN concept_reference_source crs ON crt.concept_source_id=crs.concept_source_id "
                        "WHERE crm.concept_id=%s", (concept_id,))
                    for row in cur.fetchall():
                        codings.append({"_source": row["source"], "code": row["code"]})
            except Exception:
                pass
        # caller (mapping.source_to_system) turns _source into a system URI
        from mapping import source_to_system
        codings = [{"system": source_to_system(c["_source"]), "code": c["code"],
                    **({"display": display} if display else {})} for c in codings]
        info = {"display": display, "codings": codings}
        self.cache[concept_id] = info
        return info
