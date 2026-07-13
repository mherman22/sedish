# fhir2 identifier-system seed collision (node deploy hazard)

## Symptom

After deploying the updated omods to a node, the clinical UI breaks:

```
UI Framework Error: viewProvider isanteplus does not have a view named appDashboard
```

and a cascade of modules is stopped — observed on server 44: `coreapps`,
`registrationapp`, `registrationcore`, `isanteplus`, `isanteplusreports`,
`xds-sender`, `appointmentschedulingui`, `m2sys-biometrics`,
`outgoing-message-exceptions`.

## Root cause

The custom **fhir2** build ships two liquibase seed changesets (author `Jeejen`,
dated `2026Jul07`) that `INSERT` into `fhir_patient_identifier_system` with
**hard-coded primary keys**:

| Changeset          | PK | Identifier type | URL |
|--------------------|----|-----------------|-----|
| `2026Jul07-1118`   | 3  | Code ST         | `http://isanteplus.org/openmrs/fhir2/6-code-st` |
| `2026Jul07-1119`   | 4  | Code National   | `http://isanteplus.org/openmrs/fhir2/5-code-national` |

If the node already has rows with PK 3 / 4 in `fhir_patient_identifier_system`
(from an earlier manual identifier-system fix or a prior deploy), the INSERT dies
with `Duplicate entry '3'/'4' for key 'PRIMARY'`. That **aborts fhir2 startup**,
and every stopped module transitively requires fhir2:

```
fhir2 ✗
  → mpi-client ✗, xds-sender ✗
      xds-sender ✗ → registrationcore ✗ → coreapps ✗ → isanteplus ✗
                                            (+ registrationapp, isanteplusreports,
                                             m2sys-biometrics, appointmentschedulingui,
                                             outgoing-message-exceptions)
```

This is **not** a labintegration / xds-sender version problem. Once fhir2 seeds
cleanly the whole cascade comes back up; no xds-sender backport is needed.

## Recovery (per affected node)

Run the recovery script (backs up the table, drops the two colliding rows + their
never-recorded `DATABASECHANGELOG` entries, restarts Tomcat, clears the module
cache so fhir2 re-seeds canonically):

<https://gist.github.com/mherman22/2b6c3a038778e5d4c1513446f44a8ad3>

```bash
wget -O fix.sh "https://gist.githubusercontent.com/mherman22/2b6c3a038778e5d4c1513446f44a8ad3/raw/fix-fhir2-idsystem.sh"
sudo bash fix.sh
```

Verify after ~3–4 min (want empty output):

```bash
grep -E 'Error while trying to start module|Duplicate entry|cannot be started' \
  /usr/share/tomcat7/.OpenMRS/openmrs.log | tail -30
```

## Permanent fix (belongs in the fhir2 build)

The two seed changesets must be made idempotent so they don't collide with rows
that already exist on a node. Any of:

- add a `<preConditions onFail="MARK_RAN">` guarding on the row not already
  existing (by `url`/`patient_identifier_type_id`), or
- switch the `INSERT` to `INSERT ... ON DUPLICATE KEY UPDATE`, or
- drop the hard-coded `fhir_patient_identifier_system_id` and let the PK
  auto-increment (match/lookup by identifier-type uuid instead).

Until the fhir2 build is corrected, **every node that already has seeded
`fhir_patient_identifier_system` rows will hit this on deploy** (54 and 18 both
have pre-seeded rows — 54's mapping was fixed by hand earlier). Either apply the
recovery script post-deploy, or clear PK 3/4 before deploying the new fhir2 build.

> Note: the `2026Jul07`/`Jeejen` changesets are not present in any pushed branch
> of `mherman22/openmrs-module-fhir2` at time of writing — they were added to the
> fhir2 build that was deployed. When that build's source is located, port the
> idempotency fix above into its `api/src/main/resources/liquibase.xml`.
