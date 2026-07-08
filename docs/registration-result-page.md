# After-save registration result page — structure & example

**Module:** `isanteplus-openmrs_15Dec2025` (the custom iSantePlus UI module).
**PR:** [charess-org/iSantePlus #10](https://github.com/charess-org/iSantePlus/pull/10).
**Omod:** <https://github.com/mherman22/iSantePlus/releases/download/registration-result-page/isanteplus-1.3.1-golden-result.omod>

This is the concrete implementation of §4 of [`cr-return-to-emr-design.md`](cr-return-to-emr-design.md).
Where that note is the design, this is *what got built and how the page is laid out*.

---

## 1. Why it exists

Before: after registering a patient, the registration app redirected straight back to a **blank
create-patient form** — the clinician never saw the result of the national-registry (OpenCR) dedup.

After: registration lands on a dedicated **result page** that shows the SEDISH **Golden ID** and the
patient's **cross-facility record**, then offers a button to continue into the clinical dashboard.

The lever is one config line — `afterCreatedUrl` in `isanteplus_registration_app.json`:

```json
"afterCreatedUrl": "/isanteplus/registrationResult.page?patientId={{patientId}}"
```

> Only fires when registration runs through **registrationapp** (which honors `afterCreatedUrl`),
> not the legacy registration module. Confirm the active flow at test time.

---

## 2. Page structure

```
┌──────────────────────────────────────────────────────────────┐
│  [standardEmrPage decorator — iSantePlus banner + patient hdr] │
├──────────────────────────────────────────────────────────────┤
│  ┌── info-section ─────────────────────────────────────────┐  │
│  │  👤  Résultat du registre national (SEDISH)             │  │  ← header
│  │ ─────────────────────────────────────────────────────── │  │
│  │  <PatientName>  —  <sex> · <birthdate>                   │  │  ← identity line
│  │  Identifiant SEDISH (Golden ID) : <ECID value>          │  │  ← golden id (or pending)
│  └─────────────────────────────────────────────────────────┘  │
│                                                                │
│  { registrationapp :: summary/continuityOfCare fragment }      │  ← cross-facility record
│                                                                │
│  [ Continuer vers le dossier du patient ]                      │  ← button → clinician dashboard
└──────────────────────────────────────────────────────────────┘
```

Four parts, top to bottom:

| # | Part | Source | Notes |
|---|------|--------|-------|
| 1 | Header + patient identity | `patient` (page model) | name, sex, birthdate |
| 2 | **Golden ID** | `ECID` identifier on the patient | written by `santedb-mpiclient` after OpenCR dedup |
| 3 | Cross-facility record | `registrationapp` `summary/continuityOfCare` fragment | existing CHARESS fragment (CCD/IPS occurrences) |
| 4 | Continue button | link to `coreapps/clinicianfacing/patient.page` | leaves the result page for the chart |

---

## 3. Where the Golden ID comes from

The page **does not call OpenCR**. It reads the Golden ID that the `santedb-mpiclient` module has
already resolved and stored locally as the patient's **`ECID`** identifier (see the write-side flow in
`cr-return-to-emr-design.md` §1). The controller is deliberately thin:

```java
// RegistrationResultPageController.java
public void controller(PageModel model, @RequestParam("patientId") Patient patient) {
    model.addAttribute("patient", patient);

    String goldenId = null;
    PatientIdentifier ecid = patient.getPatientIdentifier("ECID");   // stored by mpi-client
    if (ecid != null) {
        goldenId = ecid.getIdentifier();
    }
    model.addAttribute("goldenId", goldenId);
}
```

**Graceful degradation** — if the `ECID` is absent (site offline, or the background MPI sync is still
in flight) the page shows a pending message instead of failing.

---

## 4. Example — rendered

### 4a. Online, patient deduplicated (Golden ID present)

```
Résultat du registre national (SEDISH)
────────────────────────────────────────
Jean Baptiste Pierre  —  M · 12 Mar 1988
Identifiant SEDISH (Golden ID) : 5c8f2a41-9e77-4d3b-b0aa-1c2d3e4f5a6b

Dossier inter-sites
  • Hôpital St-Nicolas (Saint-Marc)   ID local: 100427   score 9.0
  • Clinique Bon Sauveur (Cange)      ID local: 55231    score 8.0

[ Continuer vers le dossier du patient ]
```

### 4b. Offline / sync in flight (Golden ID pending)

```
Résultat du registre national (SEDISH)
────────────────────────────────────────
Jean Baptiste Pierre  —  M · 12 Mar 1988
Identifiant SEDISH en attente (hors-ligne ou synchronisation en cours).

Dossier inter-sites
  (aucune occurrence disponible)

[ Continuer vers le dossier du patient ]
```

---

## 5. Files (PR #10)

```
isanteplus-openmrs_15Dec2025/omod/src/main/
├── java/.../isanteplus/page/controller/RegistrationResultPageController.java   (new)
├── webapp/pages/registrationResult.gsp                                         (new)
└── resources/apps/isanteplus_registration_app.json    (afterCreatedUrl → result page)
```

The `.gsp` uses **French string literals** (not `ui.message` keys) so it renders correctly without
adding entries to `messages_fr.properties`.

---

## 6. Deploy

1. Drop the omod into `modules/` of the running iSantePlus instance and restart (or upload via
   *Administration → Manage Modules*).
2. Register a test patient through the registration app.
3. Expect the result page instead of a blank create form; the Golden ID appears once the mpi-client
   has synced the patient to OpenCR (§3).

---

## 7. How this extends to clinical (later)

The page is the render surface for the Golden-ID-keyed bundle described in
[`cr-return-to-emr-design.md`](cr-return-to-emr-design.md). Today part 3 shows demographic occurrences
via the `continuityOfCare` fragment. When SHR clinical data is added to the same bundle, it slots into
this page as an additional section — **no new page, no re-architecture.**
