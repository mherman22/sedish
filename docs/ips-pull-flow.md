# Pulling a patient's record as an IPS — CR + SHR (read side)

**Audience:** CHARESS + DIGI/HIE team.
**Status:** implemented, PRs open (see below). This is the read-side companion to the write-side feed
([`charess-emr-realtime-mpi-setup.md`](charess-emr-realtime-mpi-setup.md)) and the design note
[`cr-return-to-emr-design.md`](cr-return-to-emr-design.md).

## What it does

When iSantePlus wants a patient's cross-facility record, it sends **only its own iSantePlus ID**. A
mediator resolves that to the **golden record** in the Client Registry (OpenCR), pulls the patient's
clinical data from the **SHR**, assembles an **IPS** (International Patient Summary) document, and
returns it. The EMR never talks to OpenCR directly — CR + SHR access live behind the mediator (this is
the "split data" the spec calls for: identity in the CR, clinical in the SHR).

```
iSantePlus (dashboard "Registre national" button / continuity of care)
  └─ xds-sender sends the patient's iSantePlus ID
      └─ GET /SHR/fhir/ips/Patient/isanteplus/<iSantePlusID>     (OpenHIM → SHR mediator)
           ├─ resolve golden record in OpenCR (identifier → golden → all linked sources)
           ├─ gather clinical from SHR per source id, rewrite subject refs to the golden
           └─ assemble IPS (Composition + merged golden Patient + clinical) → return
  ◄─ iSantePlus stores/displays the IPS
```

## Components

### 1. SHR mediator — `shared-health-record`  (PR DIGI-UW/shared-health-record #148)
- **Route:** `GET /ips/Patient/isanteplus/:id` (`src/routes/ips.ts`). Calls the generic
  `resolveGoldenAndSources(app:isantePlusSystem, id)` → golden + all linked sources →
  `generateConsolidatedIpsBundle`.
- **Golden resolution:** queries OpenCR `Patient?identifier=<isantePlusSystem>|<id>&_include=Patient:link`,
  finds the record tagged `5c827da5-4858-4f3d-a50c-62ece001efea`, then re-queries by golden id for the
  golden + every `seealso` source.
- **Clinical gathering:** for each source id, `GET {type}?patient=Patient/{sourceId}&_count=200` across
  the IPS clinical types (Condition, Observation, MedicationRequest/Statement, AllergyIntolerance,
  Immunization, Procedure, DiagnosticReport, ServiceRequest, Encounter). Subject refs are rewritten to
  the golden id; demographics are merged from golden+sources (the golden is bare in this topology).
- **Other entry points on the same route:** `/Patient/cruid/:id` (golden id), `/Patient/fpnid/:id`
  (biometric national id), `/Patient/:id` (source key = `app:mpiSystem`).

### 2. xds-sender — `openmrs-module-xds-sender`  (PR IsantePlus/openmrs-module-xds-sender #113)
- `ShrRetriever.fetchIps(patient)` now sends the patient's **iSantePlus ID** to
  `<ipsEndpoint>/Patient/isanteplus/<id>` and **no longer resolves the golden client-side** (dropped the
  direct OpenCR/CRUID lookup). Falls back to legacy client-side CCD only when `ipsEndpoint` is blank.
- Trigger points (unchanged): the patient-dashboard **"Registre national (SEDISH)"** button
  (`retrieveIps`/`downloadAndSaveCcd`), and `registrationcore` `importIps`/continuity-of-care. The
  returned bundle is stored as the patient's CCD document and rendered.

## Configuration

### OpenHIM
The existing channel already covers it — no new channel needed:
- `urlPattern: ^/SHR/fhir/ips.*$` → path-transform to the SHR mediator `/ips`, host `shr:3000`,
  `authType: private`. So external `/SHR/fhir/ips/Patient/isanteplus/<id>` reaches the route.

### SHR mediator (`config` / env)
| Key | Value |
|---|---|
| `app:isantePlusSystem` | `http://isanteplus.org/openmrs/fhir2/3-isanteplus-id` (default shipped) |
| `clientRegistryUrl` | `http://openhim-core:5001/CR/fhir` (existing) |
| `fhirServer:baseURL` | `http://shr-fhir:8080/fhir` (existing SHR FHIR store) |

### xds-sender (per site, global properties)
| Property | Value |
|---|---|
| `xdssender.ipsEndpoint` | `https://openhimcore.sedishtest.live/SHR/fhir/ips` |
| `xdssender.oshr.username` / `xdssender.oshr.password` | the OpenHIM client credentials |

> When `xdssender.ipsEndpoint` is set, retrieval uses the mediator IPS path; blank = legacy CCD.

## Deploy (once #148 + #113 merge)
1. **SHR mediator:** deploy the image built by the repo CI (`build-and-deploy`); ensure the running
   mediator registers the `^/SHR/fhir/ips.*$` channel and `app:isantePlusSystem` is set.
2. **xds-sender:** install the omod built by CI on the target iSantePlus instances; set
   `xdssender.ipsEndpoint` per site (above) and restart.
3. **Verify:** open a patient chart → **Registre national (SEDISH)** → the IPS renders with clinical
   pulled from the SHR under the golden record. Direct check:
   `GET https://openhimcore.sedishtest.live/SHR/fhir/ips/Patient/isanteplus/<iSantePlusID>` (basic auth)
   returns an IPS `document` Bundle.

## Notes / dependencies
- The SHR PR stacks on the unmerged consolidated-IPS (#144) + fpnid work, so it carries those commits.
- The mediator gathers clinical **per source id** because the SHR holds no Patient demographics
  (demographics-out topology); this is why golden resolution must expand to all linked sources.
- Related: [`shr_mediator_consolidated_ips`], [`cr-return-to-emr-design.md`](cr-return-to-emr-design.md),
  [`registration-result-page.md`](registration-result-page.md) (the dashboard button that triggers this).
