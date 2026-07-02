# Enabling the real-time MPI feed on an iSantePlus instance

**Audience:** CHARESS (owners of the iSantePlus/OpenMRS instances)
**Goal:** make each iSantePlus instance send patient demographics **in real time** to the
national Client Registry (OpenCR) through OpenHIM, as each patient is created/updated.

This document covers **only what has to be done on the iSantePlus instance**. The HIE side
(OpenHIM channel, OpenCR accept-list, per-facility OpenHIM client) is handled by the DIGI/HIE team.

---

## 0. What the HIE team gives you (per facility)

Before you start, the HIE team will provide, for **each** facility instance:

| Item | Example | Use |
|---|---|---|
| **OpenHIM client ID** | `hueh` | Basic-Auth username the instance presents |
| **OpenHIM client password** | *(provided securely)* | Basic-Auth password |
| **OpenHIM routing endpoint** | `https://openhimcore.sedishtest.live` | base URL for all feeds |

> ⚠️ Use the **`openhimcore.*`** host (the transaction router). Do **not** use `openhimconsole.*`
> — that is the admin UI; POSTing patients there silently does nothing.

The Client Registry already accepts the standard iSantePlus identifier system URIs
(`http://isanteplus.org/openmrs/fhir2/...`), so **no change is required from you on the OpenCR side**.

---

## 1. Prerequisites on the instance

1. **santedb-mpiclient module** installed and **started**, in a version that supports the FHIR
   feed (`FhirMpiClientServiceImpl`) — e.g. `1.1.5` or newer.
2. **fhir2 module** started (it is what serializes the patient to FHIR).
3. **Outbound HTTPS (443)** from the instance to `openhimcore.sedishtest.live` must be allowed
   by the server/security-group firewall.

Quick outbound check from the instance shell (replace credentials):
```bash
curl -v -u '<CLIENT_ID>:<CLIENT_PASSWORD>' \
  "https://openhimcore.sedishtest.live/CR/fhir/Patient?_summary=count"
```
Expect **HTTP 200** and a FHIR `Bundle`. Anything else (hang, `Could not resolve host`, `401`)
must be resolved before continuing.

---

## 2. Seed the FHIR identifier systems (database) — **the critical step**

By default the fhir2 module emits patient identifiers with **no `system`**, and OpenCR rejects
those with *"Patient resource has no identifier for internalid"* (HTTP 500). You must populate
the `fhir_patient_identifier_system` table so each identifier type carries the correct system URI.

**First, verify the identifier types on the instance:**
```sql
SELECT patient_identifier_type_id, uuid, name FROM patient_identifier_type
WHERE name IN ('iSantePlus ID','Code National','Code ST','Code PC',
               'Biometrics National Reference Code');
```

**Then seed the mappings** (idempotent — safe to re-run; matches by name):
```sql
INSERT INTO fhir_patient_identifier_system
  (patient_identifier_type, url, name, creator, date_created, retired, uuid)
SELECT t.patient_identifier_type_id, m.url, t.name, 1, NOW(), 0, UUID()
FROM patient_identifier_type t
JOIN (
  SELECT 'iSantePlus ID' name,                      'http://isanteplus.org/openmrs/fhir2/3-isanteplus-id' url UNION ALL
  SELECT 'Biometrics National Reference Code',      'http://isanteplus.org/openmrs/fhir2/6-biometrics-national-reference-code' UNION ALL
  SELECT 'Code National',                           'http://isanteplus.org/openmrs/fhir2/5-code-national' UNION ALL
  SELECT 'Code ST',                                 'http://isanteplus.org/openmrs/fhir2/6-code-st' UNION ALL
  SELECT 'Code PC',                                 'http://isanteplus.org/openmrs/fhir2/9-code-pc'
) m ON m.name = t.name
WHERE NOT EXISTS (
  SELECT 1 FROM fhir_patient_identifier_system f
  WHERE f.patient_identifier_type = t.patient_identifier_type_id
);
```

The two mappings that are strictly required are **iSantePlus ID** (so OpenCR accepts the patient)
and **Biometrics National Reference Code** (so the fingerprint auto-match rule works). The others
improve matching/display.

> This is the same seed used by the reference deployment; it just has to exist on **each** instance.

---

## 3. Set the mpi-client global properties

Set these via **Administration → Advanced Settings** (Manage Global Properties), filter `mpi-client`,
or via REST. Replace `<CLIENT_ID>` / `<CLIENT_PASSWORD>` with the facility's OpenHIM credentials.

| Global property | Value |
|---|---|
| `mpi-client.endpoint.format` | `fhir` |
| `mpi-client.security.authType` | `basic` |
| `mpi-client.endpoint.cr.addr` | `https://openhimcore.sedishtest.live/CR/fhir` |
| `mpi-client.endpoint.pix.addr` | `https://openhimcore.sedishtest.live/CR/fhir` |
| `mpi-client.endpoint.pdq.addr` | `https://openhimcore.sedishtest.live/CR/fhir` |
| `mpi-client.msg.sendingApplication` | `<CLIENT_ID>`  *(Basic-Auth username)* |
| `mpi-client.security.authtoken` | `<CLIENT_PASSWORD>`  *(Basic-Auth password)* |
| `mpi-client.msg.sendingFacility` | `<CLIENT_ID>` |
| `mpi-client.backgrounThreads` | `true` |

Key points:
- **`cr.addr`** is only the on/off trigger. The patient is actually **POSTed to `pix.addr`** and
  searched via `pdq.addr` — all three must be set to the `/CR/fhir` endpoint. (A default `pix.addr`
  of `127.0.0.1` is the most common reason "nothing reaches OpenHIM".)
- `sendingApplication` is the Basic-Auth **username**, `authtoken` is the **password**.
- Values are case-sensitive and must exactly match the OpenHIM client ID/password.

REST alternative (per property):
```bash
curl -u '<OMRS_ADMIN>:<PW>' -H 'Content-Type: application/json' \
  -X POST "http://localhost:8080/openmrs/ws/rest/v1/systemsetting/mpi-client.endpoint.pix.addr" \
  -d '{"value":"https://openhimcore.sedishtest.live/CR/fhir"}'
```

---

## 4. Restart OpenMRS

Restart the OpenMRS/Tomcat process (or the container) so the fhir2 module reloads the identifier
systems and the mpi-client picks up the new configuration.

---

## 5. Verify end-to-end

1. **Create a test patient** in the instance (any facility ID type is fine).
2. **On the instance**, tail the OpenMRS log and confirm the sync fired without error:
   ```bash
   grep -iE "mpi-client|MpiClient|SanteDB|PatientUpdateWorker|authenticate|UnknownHost|Connection" \
     ~/.OpenMRS/openmrs.log
   ```
3. **In the OpenHIM Console** (transaction log): you should see a **`POST /CR/fhir`** transaction
   from the facility's client returning **`200`/`201`**. (A `PUT` from the fhir-router is the
   *batch* pipeline — the real-time feed is the client's own `POST`.)

If step 3 shows the `POST /CR/fhir` at `200/201`, the real-time MPI feed is working.

---

## Troubleshooting (symptoms we have actually hit)

| Symptom | Cause | Fix |
|---|---|---|
| **Nothing appears in OpenHIM at all** | Request never leaves the instance | `cr.addr` empty, or wrong host (`openhimconsole` instead of `openhimcore`), or `pix.addr` still `127.0.0.1`, or egress blocked. Check the OpenMRS log + the §1 curl. |
| `POST /CR/fhir` → **500**, log says *"no identifier for internalid"* | fhir2 sent identifiers with no `system` | The §2 DB seed is missing or OpenMRS wasn't restarted after seeding. |
| `POST /CR/fhir` → **401** | Auth mismatch | `sendingApplication`/`authtoken` don't match the OpenHIM client ID/password (case-sensitive). |
| Request lands on the admin UI, returns HTML `200`, no transaction | Endpoint points at `openhimconsole.*` | Switch all endpoints to `openhimcore.*`. |
| `UnknownHostException` / connection refused / timeout in the log | Instance can't reach the HIE | Open outbound 443 to `openhimcore.sedishtest.live`; confirm DNS resolves. |

---

## Appendix — identifier system reference

These are the system URIs OpenCR accepts (already configured on the HIE side). The seed in §2
makes fhir2 stamp them:

| Identifier type | System URI |
|---|---|
| iSantePlus ID | `http://isanteplus.org/openmrs/fhir2/3-isanteplus-id` |
| Biometrics National Reference Code | `http://isanteplus.org/openmrs/fhir2/6-biometrics-national-reference-code` |
| Code National | `http://isanteplus.org/openmrs/fhir2/5-code-national` |
| Code ST | `http://isanteplus.org/openmrs/fhir2/6-code-st` |
| Code PC | `http://isanteplus.org/openmrs/fhir2/9-code-pc` |

> For strict multi-facility separation, a facility may instead use URIs unique to it
> (e.g. `http://<facility-domain>/openmrs/fhir2/3-isanteplus-id`). If so, tell the HIE team so
> those URIs can be added to the OpenCR accept-list before go-live.
