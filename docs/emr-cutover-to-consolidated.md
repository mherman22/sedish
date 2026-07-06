# iSantePlus cutover: from direct EMR→SHR to the consolidated pipeline

**Audience:** CHARESS (owners of the iSantePlus/OpenMRS instances).
**Goal:** move each instance from the **old** clinical workflow (EMR posts clinical documents
directly through OpenHIM to the SHR) to the **new** one (clinical data flows **EMR → consolidated
server → FHIR pipeline → SHR**), while keeping **demographics going real-time to OpenCR**.

This is **configuration + one DB seed per instance** — no code changes on the instance. Every step
below is performed **by CHARESS** on each instance (needs OpenMRS admin + database access).

---

## 1. Before / after

```
OLD (turn this off):
  iSantePlus ──clinical (xds-sender: CCD/XDS)──► OpenHIM ──► SHR
  iSantePlus ──demographics (mpi-client)───────► OpenHIM ──► OpenCR

NEW (target):
  iSantePlus ──demographics (mpi-client, real-time)──► OpenHIM ──► OpenCR
  iSantePlus DB ──site sync──► consolidated server ──► FHIR pipeline ──► OpenHIM ──► SHR (clinical)
                                                                              └────► OpenCR (reconcile)
```

Net result: **the SHR has exactly one inbound path** (consolidated → pipeline). The EMR stops
posting clinical data directly. Demographics still reach OpenCR immediately on patient save.

Two independent changes per instance:
- **A. Disable** the old clinical egress (`xds-sender`).
- **B. Enable/confirm** the demographics → OpenCR feed (`mpi-client` **+ the `fhir_patient_identifier_system` DB seed**).

---

## 2. What the HIE team provides (per facility)

| Item | Example | Used in |
|---|---|---|
| OpenHIM client ID | `hueh` | `mpi-client.msg.sendingApplication` |
| OpenHIM client password | *(provided securely)* | `mpi-client.security.authtoken` |
| OpenHIM routing endpoint | `https://openhimcore.sedishtest.live` | all `mpi-client` endpoints |

> ⚠️ Use the **`openhimcore.*`** host (the transaction router). **Not** `openhimconsole.*` (that is
> the admin UI; posting patients there silently does nothing).

Prerequisites on the instance:
- **`xds-sender ≥ 2.6.1`** (so blanking its endpoint disables the push cleanly instead of throwing on save).
- A **FHIR-capable `santedb-mpiclient`** (the build that ships `FhirMpiClientServiceImpl`, i.e. the one that supports `mpi-client.endpoint.format = fhir`).
- **Outbound HTTPS (443)** from the instance to `openhimcore.sedishtest.live` allowed.

---

## A. Disable the old clinical egress (xds-sender)

Set via **Administration → Manage Global Properties** (or REST
`POST /openmrs/ws/rest/v1/systemsetting/<property>` with `{"value":"<value>"}`).

> ⚠️ **Blank the value — do NOT delete the property row.** Some of these have a non-empty built-in
> default; deleting the row makes the module **recreate it with that default on restart** and
> silently re-enable the push. Always set to an **empty string**.

| Property | Purpose | Action |
|---|---|---|
| **`xdssender.exportCcdEndpoint`** | Clinical CCD push target (OpenSHR) | **Set empty — primary kill switch** |
| `xdssender.repositoryEndpoint` | XDS document repository endpoint | Set empty (if populated) |
| `xdssender.xdsrepository.username` / `.password` | XDS repo auth | Clear (cleanup) |
| `xdssender.oshr.username` / `.password` | OpenSHR (CCD push) auth | Clear (cleanup) |

The one that matters is **`xdssender.exportCcdEndpoint`** — with no endpoint, clinical data has
nowhere to go and never leaves the EMR. Do **not** try to disable via
`xdssender.encounterTypesToProcess` (blank/`ALL` means *all* types; there is no "none").

*(Leave `xds-sender` installed — iSantePlus core `require_module`s it; you cannot uninstall it. Disabling by config is the supported way.)*

---

## B. Enable the demographics → OpenCR feed

### B1. Seed the FHIR identifier systems (database) — **the step people forget**

By default the fhir2 module emits patient identifiers with **no `system`**, and OpenCR rejects
those with *"Patient resource has no identifier for internalid"* (HTTP 500). OpenHIM/OpenCR only
recognize the patient once each identifier carries a known `system` URI. Populate
`fhir_patient_identifier_system` so fhir2 stamps them.

**First, confirm the identifier types exist** (REST, no DB access needed):
```bash
curl -u '<OMRS_ADMIN>:<PW>' \
  "http://<instance>/openmrs/ws/rest/v1/patientidentifiertype?v=custom:(uuid,name)"
```
Expect: **iSantePlus ID, Biometrics National Reference Code, Code National, Code ST, Code PC**.

**Then seed the mappings** on the instance's OpenMRS **MySQL** (idempotent; matches by name):
```sql
INSERT INTO fhir_patient_identifier_system
  (patient_identifier_type, url, name, creator, date_created, retired, uuid)
SELECT t.patient_identifier_type_id, m.url, t.name, 1, NOW(), 0, UUID()
FROM patient_identifier_type t
JOIN (
  SELECT 'iSantePlus ID' name,                 'http://isanteplus.org/openmrs/fhir2/3-isanteplus-id' url UNION ALL
  SELECT 'Biometrics National Reference Code', 'http://isanteplus.org/openmrs/fhir2/6-biometrics-national-reference-code' UNION ALL
  SELECT 'Code National',                      'http://isanteplus.org/openmrs/fhir2/5-code-national' UNION ALL
  SELECT 'Code ST',                            'http://isanteplus.org/openmrs/fhir2/6-code-st' UNION ALL
  SELECT 'Code PC',                            'http://isanteplus.org/openmrs/fhir2/9-code-pc'
) m ON m.name = t.name
WHERE NOT EXISTS (
  SELECT 1 FROM fhir_patient_identifier_system f
  WHERE f.patient_identifier_type = t.patient_identifier_type_id
);
```
The two required for the feed to work are **iSantePlus ID** (so OpenCR accepts the patient) and
**Biometrics National Reference Code** (so the fingerprint auto-match rule works). These URIs are
already in OpenCR's accept-list, so **no HIE-side change is needed**. Then **restart OpenMRS** so
fhir2 reloads the mappings.

> Only the DB seed can do this — the fhir2 module exposes no REST/FHIR endpoint for
> `fhir_patient_identifier_system`.

### B2. Set the mpi-client global properties

Filter `mpi-client` in Advanced Settings. Replace `<CLIENT_ID>`/`<CLIENT_PASSWORD>` with the
facility's OpenHIM credentials from §2.

| Property | Value |
|---|---|
| `mpi-client.endpoint.format` | `fhir` |
| `mpi-client.security.authType` | `basic` |
| `mpi-client.endpoint.cr.addr` | `https://openhimcore.sedishtest.live/CR/fhir` |
| `mpi-client.endpoint.pix.addr` | `https://openhimcore.sedishtest.live/CR/fhir` |
| `mpi-client.endpoint.pdq.addr` | `https://openhimcore.sedishtest.live/CR/fhir` |
| `mpi-client.msg.sendingApplication` | `<CLIENT_ID>` — Basic-Auth **username** |
| `mpi-client.security.authtoken` | `<CLIENT_PASSWORD>` — Basic-Auth **password** |
| `mpi-client.msg.sendingFacility` | `<CLIENT_ID>` |
| `mpi-client.backgrounThreads` | `true` — send off the request thread; never blocks/breaks a save if OpenHIM is unreachable |

Key gotchas (each has bitten us):
- **`cr.addr` is only the on/off gate.** The patient is actually **POSTed to `pix.addr`** and
  searched via `pdq.addr` — all three must point at `/CR/fhir`. A default `pix.addr` of `127.0.0.1`
  is the classic "nothing reaches OpenHIM" cause.
- **`sendingApplication` = username, `authtoken` = password**, case-sensitive, must equal the
  OpenHIM client exactly.
- Leave identity-domain props (`mpi-client.pid.local`, `.enterprise`, `.nhid`, `.correlation`,
  `.autoXref`) at their existing site values.

---

## 3. Verify the cutover

1. **Clinical no longer leaves the EMR:** complete an encounter for a test patient → no
   xds-sender push in the instance log; the encounter saves normally.
2. **Demographics reach OpenCR (real-time):** register a test patient (online) → in the OpenHIM
   console you should see a **`POST /CR/fhir`** from the facility's client returning **`200/201`**,
   and the patient appears in OpenCR within seconds. (A `PUT` from the fhir-router is the *batch*
   pipeline; the real-time feed is the client's own `POST`.)
3. **Clinical arrives via the pipeline:** after the next site→consolidated sync + pipeline cycle,
   the patient's clinical data appears in the SHR — with **no** direct EMR→SHR call.
4. **Offline-safe:** with OpenHIM unreachable, a patient save still **succeeds locally** (relies on
   `backgrounThreads=true`); it is carried later by the consolidated sync.

### Troubleshooting (symptoms we have actually hit)
| Symptom | Cause | Fix |
|---|---|---|
| Nothing in OpenHIM at all | request never leaves the instance | `cr.addr` empty, wrong host (`openhimconsole`≠`openhimcore`), `pix.addr` still `127.0.0.1`, or egress blocked. Check the OpenMRS log. |
| `POST /CR/fhir` → **500**, *"no identifier for internalid"* | fhir2 sent identifiers with no `system` | the §B1 DB seed is missing, or OpenMRS wasn't restarted after seeding. |
| `POST /CR/fhir` → **401** | auth mismatch | `sendingApplication`/`authtoken` ≠ the OpenHIM client id/password (case-sensitive). |
| Clinical still lands in SHR directly | xds-sender still enabled | `xdssender.exportCcdEndpoint` not blanked (or row deleted → recreated with default). |

---

## 4. Rollback

Fully reversible — restore the previous values:
- `xdssender.exportCcdEndpoint` (+ `repositoryEndpoint`, creds) → previous SHR/CCD values.
- The `fhir_patient_identifier_system` rows and `mpi-client` properties are additive/harmless; leave or clear them.

---

## Quick reference

**A — disable clinical egress (blank the value):**
```
xdssender.exportCcdEndpoint      →  ""    (primary kill switch)
xdssender.repositoryEndpoint     →  ""
xdssender.xdsrepository.username →  ""
xdssender.xdsrepository.password →  ""
xdssender.oshr.username          →  ""
xdssender.oshr.password          →  ""
```

**B1 — DB seed:** run the `fhir_patient_identifier_system` INSERT above, then **restart OpenMRS**.

**B2 — demographics → OpenCR:**
```
mpi-client.endpoint.format         →  fhir
mpi-client.security.authType       →  basic
mpi-client.endpoint.cr.addr        →  https://openhimcore.sedishtest.live/CR/fhir
mpi-client.endpoint.pix.addr       →  https://openhimcore.sedishtest.live/CR/fhir
mpi-client.endpoint.pdq.addr       →  https://openhimcore.sedishtest.live/CR/fhir
mpi-client.msg.sendingApplication  →  <CLIENT_ID>
mpi-client.security.authtoken      →  <CLIENT_PASSWORD>
mpi-client.msg.sendingFacility     →  <CLIENT_ID>
mpi-client.backgrounThreads        →  true
```

**Prerequisites:** `xds-sender ≥ 2.6.1`; FHIR-capable `santedb-mpiclient`; outbound 443 to `openhimcore.sedishtest.live`.

---

*Companion docs: `docs/emr-production-dual-feed.md` (rationale for the dual-feed model) and
`docs/charess-emr-realtime-mpi-setup.md` (deep-dive on the identifier-system seed + why fhir2
needs it).*
