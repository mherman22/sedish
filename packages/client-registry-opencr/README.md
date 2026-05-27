# OpenCR — Open Client Registry

OpenCR is the Master Patient Index (MPI) for the SEDISH Haiti Health Information Exchange. It de-duplicates patient records arriving from multiple iSantePlus EMR facilities and assigns each unique patient a single **golden record** (CR ID).

## Architecture

```
iSantePlus (HUEH)  ──┐
iSantePlus (La Paix) ─┤──→ OpenHIM (/CR/fhir/*) ──→ OpenCR (:3000)
iSantePlus (OFATMA) ──┘                                  │
                                                          ├── opencr-fhir (HAPI FHIR v5.1.0, :8080) — stores patient resources
                                                          ├── opencr-es (Elasticsearch, :9200) — indexes patients for matching
                                                          └── postgres (:5432) — database for opencr-fhir
```

**OpenCR does not store clinical data.** It only stores Patient resources (demographics + identifiers) for the purpose of matching and de-duplication. Clinical data (Observations, Conditions, etc.) goes through the SHR.

## How Patient Matching Works

When a patient record arrives, OpenCR:

1. **Tags** the patient with the submitting facility (e.g., `hueh`, `lapaix`)
2. **Checks** if the patient already exists by internal identifier
3. **Runs matching** against all existing patients using 10 decision rules
4. **Links** the patient to an existing or new golden record based on match results

### Match Outcomes

Each rule produces a score. The outcome depends on two thresholds:

| Outcome | Condition | What happens |
|---------|-----------|--------------|
| **Auto-match** | score >= `autoMatchThreshold` | Patient is automatically linked to the matched golden record. No human review. |
| **Potential match** | score >= `potentialMatchThreshold` and < `autoMatchThreshold` | Patient gets a new golden record but is **flagged for human review**. |
| **No match** | score < `potentialMatchThreshold` | Patient gets a new golden record. No flag. |

### The 10 Decision Rules

Rules are evaluated in order. Each rule checks specific fields using specific algorithms. All rules use `null_handling: conservative` — if a field is missing, it fails the match.

#### Identifier-Based Rules (strongest)

| Rule | What it checks | Algorithm | Auto | Potential | Notes |
|------|---------------|-----------|------|-----------|-------|
| **1** | Biometric code | exact | 2 | 2 | Single biometric match auto-merges immediately |
| **2** | Code National | exact | 2 | 2 | National ID match auto-merges immediately |
| **3** | Code National + given + family + gender + DOB | exact | 6 | 6 | National ID with demographic confirmation |
| **4** | iSantePlus ID + given + family + gender + DOB | exact | 6 | 6 | Facility ID with demographic confirmation |

Rules 1 and 2 are **the most powerful** — a single identifier match is enough to auto-merge regardless of name differences. This is by design: a patient registered at two facilities with different names but the same national ID is considered the same person.

#### Demographic-Only Rules

| Rule | What it checks | Algorithm | Auto | Potential | Notes |
|------|---------------|-----------|------|-----------|-------|
| **5** | given + family + gender + DOB | exact | 6 | 5 | All 4 must match exactly |
| **6** | given↔family (swapped) + gender + DOB | exact | 6 | 5 | Catches data entry errors where first/last name were swapped |
| **7** | given + family + gender + DOB | fuzzy (Jaro-Winkler >= 0.8, DateDamerau >= 0.8) | 6 | 5 | Catches typos in names and dates |
| **8** | given + family + gender + DOB | exact names + fuzzy DOB (DateDamerau >= 0.8) | 6 | 5 | Catches birthdate typos with exact name match |

#### Fallback Rules

| Rule | What it checks | Algorithm | Auto | Potential | Notes |
|------|---------------|-----------|------|-----------|-------|
| **9** | Code National + given + family + gender (no DOB) | exact | 6 | 5 | For patients with missing date of birth |
| **10** | Phone + given + family + gender | exact | 6 | 5 | For patients without identifiers |

### Matching Algorithms

| Algorithm | Description | Threshold |
|-----------|-------------|-----------|
| `exact` | Case-insensitive exact string match | N/A |
| `jaro-winkler-similarity` | Fuzzy string similarity (0 = no match, 1 = identical) | 0.8 |
| `DateDamerau` | Date-aware fuzzy matching that handles digit transpositions | 0.8 |

### Null Handling

All rules use `conservative` null handling: if a field is null/missing on either patient, that field is treated as **not matching**. This prevents false matches from incomplete records.

## Golden Records

A golden record is a special Patient resource that represents a unique person. It has:

- A tag: `{ code: "5c827da5-4858-4f3d-a50c-62ece001efea", display: "Golden Record" }`
- Links to all source patient records (type: `seealso`)
- No demographic data of its own

Source patient records link back to their golden record (type: `refer`).

```
Golden Record (40cc8958...)
  ├── link: seealso → Patient/400a5e4a (HERMAN MUHEREZA, from HUEH)
  └── link: seealso → Patient/5878406c (HERMAN KACHEMBA, from La Paix)

Patient/400a5e4a (HERMAN MUHEREZA)
  └── link: refer → Patient/40cc8958 (Golden Record)

Patient/5878406c (HERMAN KACHEMBA)
  └── link: refer → Patient/40cc8958 (Golden Record)
```

### When Golden Records Are Created

- **New patient, no match**: A new golden record is created and linked.
- **New patient, auto-match**: Patient is linked to the existing golden record of the matched patient.
- **New patient, potential match**: A new golden record is created (separate from the potential match). The patient is flagged for human review.

## Patient Identifiers

OpenCR recognizes these identifier systems from iSantePlus:

| Identifier | FHIR System URI | Used in Rules |
|------------|----------------|---------------|
| iSantePlus ID | `http://isanteplus.org/openmrs/fhir2/3-isanteplus-id` | Rule 4 |
| Code ST | `http://isanteplus.org/openmrs/fhir2/6-code-st` | (not in rules) |
| Code National | `http://isanteplus.org/openmrs/fhir2/5-code-national` | Rules 2, 3, 9 |
| Biometric National Reference Code | `http://isanteplus.org/openmrs/fhir2/6-biometrics-national-reference-code` | Rule 1 |
| Code PC | `http://isanteplus.org/openmrs/fhir2/9-code-pc` | (not in rules) |

**Important**: These system URIs must be mapped in iSantePlus's `fhir_patient_identifier_system` MySQL table. If an identifier appears in OpenCR without a label, its mapping is missing from that table.

## Web UI

**URL**: `https://opencr.<domain>` (e.g., `https://opencr.sedishtest.live`)

**Login**: `root@openhim.org` / `instant101`

### Pages

| Page | Path | Purpose |
|------|------|---------|
| **Home** | `/` | Search patients by name, identifier, facility. Paginated table with configurable columns. |
| **Client Detail** | `/client/:id` | View a patient's full record, linked records, break/restore matches, audit history. |
| **Review** | `/crux/#/review` | List of all patients flagged as potential or conflict matches, requiring human action. |
| **Resolve** | `/crux/#/resolve/:id` | Side-by-side comparison to merge or split patient groups. |
| **CSV Report** | `/csvreport` | Download reports from bulk CSV uploads. |
| **Add User** | `/addUser` | Create admin or deduplication users. |
| **Users List** | `/usersList` | Manage existing users (edit role, enable/disable). |

### User Roles

| Role | Access |
|------|--------|
| **admin** | Full access to all pages including user management |
| **deduplication** | Access to search, review, and resolve pages only |

### Searching for Patients

1. Go to the **Home** page
2. Use the search filters (name, identifier, facility/Point of Service)
3. Click a patient row to view their full record
4. The patient detail page shows:
   - All linked records under the same golden record (left carousel)
   - A table of matched records with demographics
   - Tabs for **Record** (current data) and **History** (audit trail)

### Reviewing Potential Matches

1. Go to **Review** (shows badge count of pending items)
2. The table shows flagged patients with: CR ID, name, source facility, reason, date
3. Click a row to open the **Resolve** page

### Resolving Matches

The Resolve page is the core workflow for human adjudication:

1. **Groups**: Patient records are organized by golden record. Each group gets a random **chemical element nickname** (Lithium, Carbon, Oxygen, etc.) as a temporary label to make them easier to distinguish.

2. **Compare**: Toggle "Full View" to expand a patient card showing all demographics and match scores. Use "Show Scores Matrix" button to see pairwise scores between all records.

3. **Merge**: Use the dropdown on a patient row to move it from one group to another. This reassigns that patient to a different golden record. You can move a single record or an entire cohort.

4. **Split**: Select "Assign to new CR ID" from the dropdown to create a new group.

5. **Save**: Click "Save Changes" in the right sidebar. A review dialog shows all pending changes before committing.

6. **Options**:
   - "Use Simplified naming?" — toggle element nicknames on/off to show actual CR IDs
   - "Include Actual CR ID with Temporary CR ID?" — show both nickname and UUID

### Breaking and Restoring Matches

On the **Client Detail** page:

- **Break Match**: Unlinks a patient from its golden record. Creates an audit record and a `brokenMatch` extension on the patient to prevent re-linking.
- **Revert Break**: Restores a previously broken match. Removes the extension and re-runs matching.

### Audit History

The **History** tab on the Client Detail page shows:

- When the patient was submitted and by which facility
- Match/break/restore events with timestamps
- Decision rule details: which rules fired, what algorithms were used, what scores were produced
- Raw Elasticsearch queries and responses (expandable advanced section)

## Configuration Files

| File | Location | Purpose |
|------|----------|---------|
| `config.json` | `packages/client-registry-opencr/config/` | Main config: FHIR server, Elasticsearch, auth, clients, identifier systems |
| `decisionRules.json` | `packages/client-registry-opencr/config/` | The 10 matching rules with algorithms and thresholds |
| `mediator.json` | `packages/client-registry-opencr/config/` | OpenHIM mediator registration |
| `PatientRelationship.json` | `packages/client-registry-opencr/config/` | Defines patient fields available in reports |

### Key Configuration Options

| Setting | Location | Purpose |
|---------|----------|---------|
| `matching.tool` | config.json | `"elasticsearch"` (production) or `"mediator"` (simpler) |
| `elastic.server` | config.json | Elasticsearch URL (`http://opencr-es:9200`) |
| `elastic.index` | config.json | Index name (`patients`) |
| `fhirServer.baseURL` | config.json | HAPI FHIR URL (`http://opencr-fhir:8080/fhir`) |
| `codes.goldenRecord` | config.json | UUID that tags golden records |
| `cronJobs.patientReprocessing` | config.json | Cron schedule for reprocessing flagged patients (default: `0 21 * * *` = 9 PM daily) |
| `auth.tokenDuration` | config.json | JWT token validity in seconds (default: 5400 = 90 min) |
| `clients[]` | config.json | List of registered facilities (id + display name) |
| `systems.*` | config.json | Maps identifier system URIs to display names for the UI |

### Registered Clients (Facilities)

These are the Point of Service (POS) entries that tag which facility submitted a patient:

| Client ID | Display Name |
|-----------|-------------|
| hueh | HUEH |
| lapaix | La Paix |
| ofatma | OFATMA |
| foyer-saint-camille | Foyer Saint Camille |
| klinik-eritaj | Klinik Eritaj |
| ofatma-sonapi | OFATMA SONAPI |
| gressierms | Gressier MS |
| pestel | Pestel |
| stdemiragoane | St Demir Agoane |
| bethel-fdn | Bethel FDN |
| openshr | Shared Health Record |
| shr-pipeline | SHR Pipeline |
| openmrs | OpenMRS |
| cr | Client Registry |

## API Endpoints

All endpoints are prefixed with `/ocrux/` and require JWT authentication (except `/user/authenticate`).

### Patient Matching

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/match/count-match-issues` | Count of patients needing review |
| GET | `/match/get-match-issues` | List all flagged patients |
| GET | `/match/potential-matches/:id` | Get match matrix for a specific patient |
| POST | `/match/resolve-match-issue` | Save match resolution decisions |
| POST | `/match/break-match` | Unlink a patient from its golden record |
| POST | `/match/unbreak-match` | Restore a previously broken match |

### FHIR

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET/POST | `/fhir/Patient` | Search or create patients |
| GET | `/fhir/Patient/:id` | Read a specific patient |
| GET | `/fhir/AuditEvent` | Query audit trail |

### User Management

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/user/authenticate` | Login (returns JWT token) |
| GET | `/user/getUsers` | List all users |
| POST | `/user/addUser` | Create a new user |
| POST | `/user/editUser` | Update user role or status |
| POST | `/user/changepassword` | Change password |
| GET | `/isTokenActive` | Validate current JWT token |

### Configuration

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/config/getClients` | List registered facilities |
| GET | `/config/getURI` | Get identifier system URI mappings |

### CSV

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/csv/getCSVUpload` | List uploaded CSV files |
| GET | `/csv/getCSVReport/:id` | Download a specific CSV report |

## Reprocessing

Patients marked with `require-reprocess` (due to concurrent submissions) are automatically reprocessed by a daily cron job at 9 PM. This re-runs matching to ensure no duplicates were missed during high-volume imports.

## Data Management

### Clearing All OpenCR Data

```bash
# Drop the FHIR database
docker exec -e PGPASSWORD=instant101 $(docker ps -q -f name=client-registry-opencr_postgres) \
  psql -U postgres -c "DROP DATABASE IF EXISTS hapi; CREATE DATABASE hapi;"
docker service update --force client-registry-opencr_opencr-fhir

# Clear the Elasticsearch index
docker exec $(docker ps -q -f name=opencr-es) curl -X DELETE http://localhost:9200/patients
docker service update --force client-registry-opencr_opencr
```

### Checking Patient Count

```bash
# In Elasticsearch
docker exec $(docker ps -q -f name=opencr-es) curl -s http://localhost:9200/patients/_count

# In FHIR
curl -sk -u openshr:openshr \
  "https://openhimcore.<domain>/CR/fhir/Patient?_summary=count&_tag:not=5c827da5-4858-4f3d-a50c-62ece001efea" \
  -H "Accept: application/fhir+json"
```
