# iSantePlus → SEDISH HIE: enabling the dual-feed on a production instance

How a production iSantePlus (OpenMRS) instance must be configured to participate in the
SEDISH consolidated-server architecture, and the exact OpenMRS **global properties** to
change — with the reason for each.

This is a **configuration change only** (global properties), plus one module-version
prerequisite. No code changes on the instance.

---

## 1. The architecture (what we are realizing)

Two kinds of data leave the site, by **two different paths**:

```
                         ┌─────────────────────────── DEMOGRAPHICS ───────────────────────────┐
                         │  (+ national fingerprint ID, if any) — REAL TIME, on patient save   │
                         ▼                                                                      │
  ┌────────────┐   mpi-client    ┌─────────┐        ┌──────────┐                               │
  │ iSantePlus │ ──────────────► │ OpenHIM │ ─────► │  OpenCR  │  (Client Registry / MPI)      │
  │   (EMR)    │   /CR/fhir       └─────────┘        └──────────┘                               │
  └─────┬──────┘                                                                                │
        │  CLINICAL data is NOT sent from the site directly. It is written locally and          │
        │  carried by the (CHARESS) site → consolidated sync, then:                             │
        ▼                                                                                       │
  ┌──────────────┐   site→consolidated   ┌───────────────┐   pipeline   ┌─────────┐   /SHR/fhir ┌─────┐
  │ local OpenMRS │ ───── sync ─────────► │ consolidated  │ ───────────► │ OpenHIM │ ──────────► │ SHR │
  │   database    │   (their scheduler,   │   database    │  (ours)      └─────────┘             └─────┘
  └──────────────┘    ~30–60 min)         └───────┬───────┘
                                                  │  the same pipeline also re-asserts
                                                  └──► DEMOGRAPHICS → OpenHIM /CR → OpenCR
                                                       (reconciler + offline catch-up)
```

### Online (internet + power at save time)
- **Demographics** (and the fingerprint national ID, if present) are sent **in real time,
  directly to OpenCR** via OpenHIM, on patient save — exactly like the legacy architecture.
- **Clinical data** is **never** sent from the site to the SHR directly. It is written to the
  local OpenMRS DB and later picked up by the site→consolidated sync.

### Offline (no internet/power at save time)
- **Nothing leaves the site in real time** — there is **no retry queue** on the site. The
  patient and any clinical data are stored **locally only**.
- When connectivity returns, the existing **site→consolidated sync** carries everything
  (demographics + clinical) to the consolidated DB, which then forwards demographics→OpenCR and
  clinical→SHR through our pipeline.

### Consequences of the model
- **The SHR has exactly one inbound path:** `consolidated DB → pipeline → OpenHIM → SHR`. There
  is no EMR→SHR path anymore.
- **OpenCR has two writers** that converge (real-time from the site + the consolidated
  pipeline). They do not duplicate because both key the patient on the same OpenMRS person
  `uuid` and on the SEDISH source key, which OpenCR upserts on.
- A patient registered **offline** is invisible to OpenCR/SHR until the next successful
  site→consolidated sync. This is by design (no retry).

---

## 2. Which module does which feed

| Feed | OpenMRS module | Direction |
|------|----------------|-----------|
| **Demographics → OpenCR** (real-time, on save) + patient search | `santedb-mpiclient` (≥ 1.1.8) with `registrationcore` | **KEEP ON** |
| **Clinical → SHR** (CCD / XDS documents) | `xds-sender` | **TURN OFF** |

The whole change is: **turn off the `xds-sender` egress, keep the `mpi-client` feed on.**

---

## 3. Prerequisite — `xds-sender ≥ 2.6.1`

Confirm (or upgrade) the **`xds-sender`** module to **2.6.1 or newer** on every production
instance *before* changing properties.

**Why:** 2.6.1 makes the sender **skip cleanly when its export endpoint is empty**. On older
versions an empty endpoint can throw during encounter save instead of skipping — which would
disrupt clinical documentation. With 2.6.1, blanking the endpoint is a safe, silent disable.

Check: Administration → Manage Modules → look for *XDS Sender*, version ≥ 2.6.1.

---

## 4. Properties to DISABLE — stops clinical leaving the EMR

Set via **Administration → Manage Global Properties**, or REST:
`POST /openmrs/ws/rest/v1/systemsetting/<property>` with body `{"value":"<value>"}`.

> ⚠️ **Blank the value — do NOT delete the property row.** Several of these have a **non-empty
> built-in default**. If you delete the row, the module **recreates it with that default on
> restart** and silently re-enables the push (e.g. `xdssender.exportCcdEndpoint` defaults back to
> `https://sedish.net:8082/openmrs/ws/rest/exportccd/ccd`). Always set the value to an **empty
> string**.

| Property | Module description / purpose | Built-in default | Action |
|----------|------------------------------|------------------|--------|
| **`xdssender.exportCcdEndpoint`** | Export-CCD endpoint (OpenSHR) — the clinical CCD push target | `https://sedish.net:8082/.../exportccd/ccd` | **Set empty** — primary kill switch |
| **`xdssender.repositoryEndpoint`** | XDS document repository endpoint | *(blank)* | **Set empty** (if populated) |
| `xdssender.xdsrepository.username` | Auth username for the XDS repository | *(blank)* | Clear (cleanup) |
| `xdssender.xdsrepository.password` | Auth password for the XDS repository | *(blank)* | Clear (cleanup) |
| `xdssender.oshr.username` | OpenSHR username (CCD push) | *(blank)* | Clear (cleanup) |
| `xdssender.oshr.password` | OpenSHR password (CCD push) | *(blank)* | Clear (cleanup) |
| `xdssender.mpiEndpoint` | xds-sender's own lookup to the OpenCR MPI (used while building the document) | *(blank)* | Clear (cleanup) |

**The one that matters is `xdssender.exportCcdEndpoint`** (and `repositoryEndpoint` if your
instance uses the XDS-document path). With no endpoint, the sender has nowhere to push, so
clinical never leaves the EMR. The rest are cleanup so no stale credentials/endpoints remain.

> Note on `xdssender.encounterTypesToProcess` — this is the *gate* for which encounter types are
> eligible to be sent. **Do not** rely on it to disable the feed: blank or `ALL` means **all**
> types are eligible, and there is no "none" value. Disable by clearing the **endpoints**, not
> the gate.

---

## 5. Properties to KEEP/SET — demographics → OpenCR (real-time)

These drive the real-time demographics feed and patient search. They must be **present and
pointed at the production OpenHIM**.

| Property | Module description / purpose | Set to |
|----------|------------------------------|--------|
| **`mpi-client.endpoint.cr.addr`** | Location of the Client Registry endpoint (the demographics target) | `https://<PROD_OPENHIM_DOMAIN>/CR/fhir` |
| `mpi-client.endpoint.pix.addr` | PIX (identity feed) endpoint | `https://<PROD_OPENHIM_DOMAIN>/CR/fhir` |
| `mpi-client.endpoint.pdq.addr` | PDQ (patient search) endpoint | `https://<PROD_OPENHIM_DOMAIN>/CR/fhir` |
| **`mpi-client.security.authtoken`** | Auth token presented to OpenHIM (the per-facility OpenHIM client) | `<FACILITY_ID>` |
| **`mpi-client.msg.sendingApplication`** | Registered application name for this endpoint (MSH-3); used as the OpenHIM client identity | `<FACILITY_ID>` |
| `mpi-client.msg.sendingFacility` | Registered facility name for this endpoint (MSH-4) | `<FACILITY_ID>` (or site code) |

> ⚠️ `mpi-client.endpoint.cr.addr` has a built-in default of
> `https://openhim.sedish-haiti.org/CR/fhir`. **Do not rely on the default** — set it explicitly
> to the production OpenHIM `/CR/fhir`, or demographics will be sent to the wrong host.

### Recommended for the offline behavior (no block on save)

| Property | Purpose | Set to | Why |
|----------|---------|--------|-----|
| `mpi-client.backgrounThreads` | Send the ADT (demographics) message on a **background thread** | `true` | Patient save returns immediately and is **not blocked** if OpenHIM/OpenCR is unreachable — matches the "no retry, never block the save" requirement. Combined with `mpi-client ≥ 1.1.8` (which null-guards an empty/failed MPI response), an offline save completes locally and is simply not pushed. |

Leave the identity-domain properties (`mpi-client.pid.local`, `mpi-client.pid.enterprise`,
`mpi-client.pid.nhid`, `mpi-client.pid.correlation`, `mpi-client.pid.autoXref`) at their
existing site values — they define how local/national identifiers are labelled to the MPI and
do not need to change for this cutover.

---

## 6. Why this configuration

- **Clinical must take one path only** (`consolidated → pipeline → SHR`) so the SHR is a single,
  consistent, deduplicated clinical store. Two clinical writers (EMR direct + consolidated)
  would create duplicate/again-keyed documents in the SHR. Hence `xds-sender` egress is off.
- **Demographics stay real-time to OpenCR** so identity/matching is current the moment a patient
  is registered (when online) — the value of the MPI is immediacy.
- **The consolidated pipeline still pushes demographics to OpenCR** as a reconciler and as the
  **only** demographics path for patients registered offline. It is idempotent (same `uuid` +
  source key), so it never duplicates the real-time feed.
- **No retry on the site** keeps the EMR simple and offline-tolerant; eventual consistency is
  provided by the site→consolidated sync, not by a queue on the instance.

---

## 7. Verify after the change

1. **xds-sender is inert:** create/complete an encounter for a test patient → confirm **no**
   outbound call to the SHR/XDS endpoint (check the instance logs; there should be no
   xds-sender push attempt), and the encounter saves normally.
2. **Demographics still flow:** register a test patient (online) → confirm the patient appears
   in **OpenCR** within seconds (via OpenHIM `/CR`), **before** any consolidated cycle.
3. **No duplicate in OpenCR:** after the same patient later flows through the consolidated
   pipeline, confirm OpenCR still has **one** source record for that patient (same `uuid`), not
   two.
4. **Offline-safe:** with OpenHIM unreachable, register a patient → the save **succeeds locally**
   with no error dialog and no hung request (relies on `backgrounThreads=true` +
   `mpi-client ≥ 1.1.8`).

---

## 8. Rollback

The change is fully reversible — restore the previous values:

- `xdssender.exportCcdEndpoint` → the previous SHR/CCD endpoint
- `xdssender.repositoryEndpoint` / `xdssender.*` credentials → previous values

(Keeping the previous values noted before the change makes rollback a copy-paste.)

---

## Quick reference

**Disable (blank the value):**
```
xdssender.exportCcdEndpoint        →  ""      (primary kill switch)
xdssender.repositoryEndpoint       →  ""
xdssender.xdsrepository.username   →  ""
xdssender.xdsrepository.password   →  ""
xdssender.oshr.username            →  ""
xdssender.oshr.password            →  ""
xdssender.mpiEndpoint              →  ""
```

**Keep / set (point at production OpenHIM):**
```
mpi-client.endpoint.cr.addr        →  https://<PROD_OPENHIM_DOMAIN>/CR/fhir
mpi-client.endpoint.pix.addr       →  https://<PROD_OPENHIM_DOMAIN>/CR/fhir
mpi-client.endpoint.pdq.addr       →  https://<PROD_OPENHIM_DOMAIN>/CR/fhir
mpi-client.security.authtoken      →  <FACILITY_ID>
mpi-client.msg.sendingApplication  →  <FACILITY_ID>
mpi-client.msg.sendingFacility     →  <FACILITY_ID>
mpi-client.backgrounThreads        →  true
```

**Prerequisite:** `xds-sender ≥ 2.6.1` installed on the instance.
