# iSantePlus "leaf mode" — disabling the old direct-to-HIE write paths

## Why

In the **consolidated architecture** the EMR is a **leaf data source**. There is exactly one
path for EMR data to reach the HIE:

```
iSantePlus (local write) ──CHARESS binlog CDC──▶ consolidated_db ──pipeline──▶ OpenCR + SHR
```

The EMR's only outbound interaction with the HIE should be **PDQ search (read)**. It must make
**no direct write** to OpenCR or the SHR.

The legacy iSantePlus modules (`santedb-mpiclient`, `xds-sender`) were built for the *old*
real-time pattern — on patient/encounter save (and on MPI import) they push straight to
`OpenHIM → OpenCR` (PIX feed) and `OpenHIM → SHR` (CCD). That pattern is being retired: it
bypasses the consolidated server, and it re-pollutes OpenCR with duplicate `potentialMatches`
source records (e.g. importing a patient creates a local copy that gets fed back to OpenCR
instead of just flowing up via CDC).

These push behaviours are gated entirely by **OpenMRS global properties** holding endpoint URLs.
Emptying those URLs disables the writes while leaving search intact.

## Properties to EMPTY (break the old write paths)

### santedb-mpiclient — emptying is clean (advice null-guards)

| Global property | What it does (old behaviour) |
|---|---|
| `mpi-client.endpoint.cr.addr` | Gates the **save-triggered PIX feed** (`PatientSynchronizationAdvice` + `EncounterSynchronizationAdvice`). On every `savePatient`/`saveEncounter` it pushes to OpenCR. The advice does `if (cr.addr == null || trim().isEmpty()) return;` → empty = clean skip, no error. |
| `mpi-client.endpoint.pix.addr` | **PIX cross-reference register.** Used by the **import** path to write the local↔MPI cross-reference back to OpenCR. (This is why emptying `cr.addr` alone is *not* enough — import still wrote via `pix.addr`.) |

### xds-sender — DON'T just empty the endpoints (it's noisy); disable the listener or remove the module

> **Status (2026-06-22): RESOLVED — emptying `exportCcdEndpoint` is now the clean off-switch.**
> The guard (IsantePlus/openmrs-module-xds-sender **PR #111**) shipped in **v2.6.1**, which is now
> bundled here (`config/custom_modules/xds-sender-2.6.1.omod`, replacing 2.6.0). With ≥ v2.6.1 a
> blank `exportCcdEndpoint` makes the listener skip cleanly (logs, no `APIException`, no error
> queue). So just **empty `xdssender.exportCcdEndpoint`** — the `encounterTypesToProcess`-dummy
> workaround below is no longer needed (it was only for the pre-2.6.1 OMOD). The
> `mpiEndpoint`/`repositoryEndpoint` default-fallback note still applies, but with the export
> skipped they are never reached.

The xds-sender endpoints do **not** empty cleanly (verified against the source):

- `xdssender.exportCcdEndpoint` is read via `getProperty(name)` **with no default → throws
  `APIException("Property value … is not set")` when blank.** The export then **throws on every
  encounter**. It is caught (the listener is async/JMS and wrapped in try/catch with the
  `outgoing-message-exceptions` handler configured), so **encounter-save and module startup are NOT
  broken** — but you get a thrown exception **+ an outgoing-exception queue entry per encounter**
  (log/DB growth).
- `xdssender.mpiEndpoint` and `xdssender.repositoryEndpoint` have **built-in defaults**, so blanking
  them does **not** disable them — they fall back to bogus/external addresses
  (`getProperty(MPI_ENDPOINT, "1.2.3.4.5")`, `getProperty(XDS_REPO_ENDPOINT,
  "http://sedish.net:5001/xdsrepository")`) and the module *attempts* to reach those.

So for xds-sender, prefer one of these instead of emptying:

| Option | How | Effect |
|---|---|---|
| **Remove the module (cleanest)** | drop the `xds-sender` (+ `outgoing-message-exceptions`) OMODs from the image | no push, no errors, no queue growth, nothing to reach |
| **Disable the listener (config-only, no errors)** | set `xdssender.encounterTypesToProcess` to a **dummy, non-existent encounter-type UUID** | the listener **skips every encounter before reading any endpoint** → no `APIException`, no queue noise. ⚠️ leaving it **null/empty means "process ALL"**, so set a real-but-non-matching value, never blank. |
| **Patch the module (proper fix)** | fork `IsantePlus/openmrs-module-xds-sender`, add a blank-endpoint guard in `EncounterEventListener.exportEncounter` (mirror mpi-client), build the OMOD, rebundle | empty `exportCcdEndpoint` becomes a **clean off-switch** (skip + log, no exception), consistent with mpi-client. Cost: maintain a fork + Maven build. |

**Suggested patch** (in `EncounterEventListener`, before calling `exportProvideAndRegister`):

```java
String ccd = Context.getAdministrationService().getGlobalProperty("xdssender.exportCcdEndpoint");
if (org.apache.commons.lang3.StringUtils.isBlank(ccd)) {
    log.info("xds-sender: exportCcdEndpoint blank, skipping export (leaf mode)");
    return;
}
```
This is exactly the pattern `santedb-mpiclient`'s `PatientSynchronizationAdvice` already uses for
`cr.addr`. Upstreaming it to IsantePlus would benefit everyone.

> If you still choose to empty `exportCcdEndpoint` (e.g. as a quick stop-gap), know it works but
> emits a handled exception + a queue entry on every encounter.

## Properties to KEEP (read-only search must keep working)

| Global property | Why keep |
|---|---|
| `mpi-client.endpoint.pdq.addr` | **PDQ demographic search** — the clinician searches the registry at registration. This is a read; it stays pointed at `http://openhim-core:5001/CR/fhir`. |
| `mpi-client.security.authtoken` | per-facility OpenHIM client password used to authenticate the PDQ search. |
| `mpi-client.msg.sendingApplication` | per-facility OpenHIM client id (username) for the PDQ search. |

## The gotcha: why "deleting" or "not setting" them does NOT work

OpenMRS **auto-creates** any global property a module declares in its `config.xml`, using the
declared `defaultValue`, whenever the row is **absent** at module startup. Both endpoints default
to **live URLs**:

| Property | config.xml default that gets recreated |
|---|---|
| `mpi-client.endpoint.cr.addr` | `https://openhim.sedish-haiti.org/CR/fhir` |
| `xdssender.exportCcdEndpoint` | `https://sedish.net:8082/openmrs/ws/rest/exportccd/ccd` |

So:

- **Deleting the GP row** → recreated as the default URL on next boot → write path reopens.
- **Removing the `post-start.sh` line** → value falls back to the dump/config default (a URL).

The durable fix is to make the property **exist with an empty value**, set on **every boot**.
OpenMRS will not overwrite an existing (even empty) GP with the default.

**Null is safe.** Setting an empty value via the REST `systemsetting` API stores it as `null`.
The advices guard `crEndpoint == null || crEndpoint.trim().isEmpty()` (verified in the deployed
`santedb-mpiclient` bytecode), so null/empty both skip the feed cleanly — no NPE, patient-save
unaffected.

## How to apply

### Persisted (all deployments) — `packages/emr-isanteplus/config/post-start.sh`

`post-start.sh` runs after OpenMRS boots and overrides whatever the modules/dump created. Set the
write endpoints empty there, and keep the search settings:

```sh
# Break old direct-to-HIE write paths (leaf mode). Ingestion is via CHARESS CDC.

# mpi-client: empty endpoints disable the PIX feed cleanly (advice null-guards).
set_property "mpi-client.endpoint.cr.addr"  ""
set_property "mpi-client.endpoint.pix.addr" ""

# xds-sender (>= v2.6.1, bundled): empty exportCcdEndpoint -> listener skips cleanly (PR #111).
set_property "xdssender.exportCcdEndpoint" ""

# Keep read-only PDQ search working
set_property "mpi-client.endpoint.pdq.addr"      "http://openhim-core:5001/CR/fhir"
set_property "mpi-client.security.authtoken"     "${FACILITY}"
set_property "mpi-client.msg.sendingApplication" "${FACILITY}"
```

> **Cleaner still:** drop the `xds-sender` and `outgoing-message-exceptions` OMODs from the image
> (`packages/emr-isanteplus/config/custom_modules/`) — then there's nothing to disable and no
> error handler to keep around. `santedb-mpiclient` stays for PDQ search.

Then rebuild the management image and redeploy the EMR package (per-facility — applies to all
iSantePlus instances).

### Running instances (apply now, no redeploy) — REST

For each instance container (`isanteplus_isanteplus.*`, `isanteplus_isanteplus2.*`, …):

```sh
B=http://localhost:8080/openmrs/ws/rest/v1/systemsetting
post(){ curl -sf -u admin:Admin123 -X POST -H 'Content-Type: application/json' \
         -d "{\"value\":\"$2\"}" "$B/$1" -o /dev/null; }
post mpi-client.endpoint.cr.addr  ""
post mpi-client.endpoint.pix.addr ""
post xdssender.exportCcdEndpoint  ""
```

## Verify

1. Each property reads back empty/null; `pdq.addr` still set.
2. **Search still works** (PDQ returns results in the registration screen).
3. **No EMR write reaches the HIE:** in the OpenHIM transaction log, after creating/importing a
   patient there are **no** `POST/PUT /CR/fhir` or `POST /SHR/fhir` transactions from the
   **facility client** (`hueh`, `lapaix`, …). Only the `consolidated` client writes to OpenCR.
4. The new patient subsequently appears in OpenCR via the **pipeline/CDC path** (tagged
   `consolidated-pipeline`), matched to its golden by code-national — not as a facility-tagged
   `potentialMatches` record.

## Known separate issue (not fixed by this)

Emptying the endpoints stops the **MPI re-pollution**, but the `santedb-mpiclient` **import**
still has a local-duplication bug: importing one MPI patient follows `_include=Patient:link` and
creates a **local patient per linked record** (the source *and* its golden), so one import can
create two local patients. That is independent of the write endpoints and needs a separate fix
(import only the golden / make import idempotent).

## Cleaner alternative

Since `xds-sender`'s only job is the push being retired (and `outgoing-message-exceptions` only
services failed xds/mpi sends), you can **remove those two OMODs** from the image entirely instead
of emptying their endpoints — leaving `santedb-mpiclient` solely for PDQ search. `mpi-client`
cannot be removed (it provides the search).
