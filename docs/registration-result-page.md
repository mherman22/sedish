# After-save registration result page — structure & example

**Module:** `isanteplus-openmrs_15Dec2025` (the result page) + `registration` (the flow).
**PRs:** [#10](https://github.com/charess-org/iSantePlus/pull/10) (result page),
[#12](https://github.com/charess-org/iSantePlus/pull/12) (registration flow: guards + redirect),
[#13](https://github.com/charess-org/iSantePlus/pull/13) (dashboard button).
**Omods:** `isanteplus-1.3.1.omod`, `registration-1.0.0-SNAPSHOT.omod`
(<https://github.com/mherman22/iSantePlus/releases/tag/registration-result-page>).

This is the concrete implementation of §4 of [`cr-return-to-emr-design.md`](cr-return-to-emr-design.md).

---

## 1. What it is & how it's reached

A dedicated **result page** (`isanteplus/registrationResult.page?patientId=…`) that queries OpenCR
live and shows the SEDISH **Golden ID** and the **cross-facility occurrences the Client Registry holds
for the patient** (via `MpiClientService.getGoldenRecordOccurrences`).

**How it's reached — on demand, from the patient dashboard.** A **"Registre national (SEDISH)"**
action button on the clinician-facing patient dashboard (`patientDashboard.overallActions` extension in
`isantepluspatientdashboard_extension.json`) opens the page for the current patient. So a clinician can
re-query the registry from any chart, anytime.

**Registration flow (important correction).** Patient registration on these instances runs through the
custom **`registration`** module (`registration/register.page`), **not** `registrationapp` — so the
`afterCreatedUrl` config is irrelevant here. After a successful save the `registration` module now
redirects to the **clinician-facing patient dashboard** (`coreapps/clinicianfacing/patient.page`), not
to the result page. (Earlier the redirect went straight to the result page; that was moved to the
on-demand button so registration lands on the chart.)

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
│  ┌── info-section ─────────────────────────────────────────┐  │
│  │  🔍  Correspondances dans le registre national          │  │  ← MPI matches header
│  │ ─────────────────────────────────────────────────────── │  │
│  │  Nom | Sexe | Date de naissance | Identifiants | Site   │  │  ← one row per match
│  │  ...                                                     │  │     (or "aucune"/"indisponible")
│  └─────────────────────────────────────────────────────────┘  │
│                                                                │
│  { registrationapp :: summary/continuityOfCare fragment }      │  ← cross-facility clinical record
│                                                                │
│  [ Continuer vers le dossier du patient ]                      │  ← button → clinician dashboard
└──────────────────────────────────────────────────────────────┘
```

Five parts, top to bottom:

| # | Part | Source | Notes |
|---|------|--------|-------|
| 1 | Header + patient identity | `patient` (page model) | name, sex, birthdate |
| 2 | **Golden ID** | `ECID` identifier on the patient | written by `santedb-mpiclient` after OpenCR dedup |
| 3 | **Matches across sites** | `MpiClientService.searchPatient(patient, null)` | one row per record OpenCR holds for this person: name, sex, DOB, identifiers, site |
| 4 | Cross-facility clinical record | `registrationapp` `summary/continuityOfCare` fragment | existing CHARESS fragment (CCD/IPS) |
| 5 | Continue button | link to `coreapps/clinicianfacing/patient.page` | leaves the result page for the chart |

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

## 3b. Where the matches come from

The **matches** section (part 3) asks the Client Registry, at page load, what records it holds for
this person: `MpiClientService.searchPatient(patient, null)` → `List<MpiPatient>`. Each row is a
`MpiPatient` (a `Patient` subclass carrying an extra `sourceLocation`) rendered as name, sex, DOB,
identifiers, and site.

Two implementation choices worth noting:

- **No hard dependency on mpi-client.** The controller resolves the service *reflectively* through
  OpenMRS' cross-module class loader (`Context.loadClass(...)` → `Context.getService(...)`), so this
  UI module carries no build/runtime dependency on the mpi-client module. If mpi-client is not
  installed, the section shows *"Recherche indisponible"* rather than the module failing to start.
- **`null` second argument is safe** thanks to the mpi-client NPE guard (PR #67): `searchPatient`
  tolerates a null `otherDataPoints` map.

Three render states:

| State | Trigger | Shown |
|-------|---------|-------|
| Rows | MPI returned ≥1 match | the matches table |
| Empty | MPI reachable, 0 matches | *"Aucune correspondance trouvée dans le registre."* |
| Unavailable | offline / PDQ not configured / mpi-client absent | *"Recherche indisponible (hors-ligne ou registre non joignable)."* |

> **Score not shown.** OpenCR computes a match score, but the mpi-client `MpiPatient` model does not
> currently carry it, so the table omits it. Surfacing the score would need a small model extension in
> the mpi-client module.

---

## 4. Example — rendered

### 4a. Online, patient deduplicated (Golden ID + matches present)

```
Résultat du registre national (SEDISH)
────────────────────────────────────────
Jean Baptiste Pierre  —  M · 12 Mar 1988
Identifiant SEDISH (Golden ID) : 5c8f2a41-9e77-4d3b-b0aa-1c2d3e4f5a6b

Correspondances dans le registre national
────────────────────────────────────────
Nom                   | Sexe | Date de naissance | Identifiants                      | Site
Jean Baptiste Pierre  | M    | 12 Mar 1988       | iSantePlus ID: 100427             | Hôpital St-Nicolas (Saint-Marc)
Jean B. Pierre        | M    | 12 Mar 1988       | iSantePlus ID: 55231             | Clinique Bon Sauveur (Cange)

Dossier inter-sites (continuityOfCare)
  ...

[ Continuer vers le dossier du patient ]
```

### 4b. Offline / sync in flight (Golden ID pending, MPI unreachable)

```
Résultat du registre national (SEDISH)
────────────────────────────────────────
Jean Baptiste Pierre  —  M · 12 Mar 1988
Identifiant SEDISH en attente (hors-ligne ou synchronisation en cours).

Correspondances dans le registre national
────────────────────────────────────────
Recherche indisponible (hors-ligne ou registre non joignable).

[ Continuer vers le dossier du patient ]
```

---

## 5. Files

```
isanteplus-openmrs_15Dec2025/omod/src/main/            (result page + dashboard button)
├── java/.../isanteplus/page/controller/RegistrationResultPageController.java   (new — PR #10)
├── webapp/pages/registrationResult.gsp                                        (new — PR #10)
└── resources/apps/isantepluspatientdashboard_extension.json  (dashboard button — PR #13)

registration/omod/src/main/                            (registration flow)
├── java/.../registration/page/controller/RegisterPageController.java   (redirect → dashboard, guards — PR #12)
└── webapp/pages/register.gsp                                           (date-step fix, no-dup guard, redirect — PR #12)
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
