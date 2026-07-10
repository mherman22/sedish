# mpi-client SEDISH source-key upsert — implementation spec (Option C)

**Goal:** make the site real-time feed converge with the consolidated batch feed in OpenCR, so a
patient fed by **both** paths (required for offline/power resilience) resolves to **one source
record**, not two. See [`opencr-dual-feed-convergence.md`](opencr-dual-feed-convergence.md).

**Mechanism:** both feeds carry the identical **SEDISH source-key** identifier
(`<mspp_code>-<patient_id>`, system `http://sedish-haiti.org/fhir/source-key`) and **upsert** on it.
The consolidated ETL already does this; this spec adds it to `santedb-mpiclient`.

## Prerequisites
- **patient_id parity — confirmed.** The ETL's source-key uses the consolidated DB's `patient_id`,
  which is the site's OpenMRS `patient.patient_id` preserved by replication (the CHARESS idempotency
  key). The real-time feed will use `patient.getPatientId()` → identical value. (Corroborated: iSante
  ID `751010037` ↔ source-key `75101-37`, i.e. patient_id `37` on both.)
- **OpenCR cross-clientid upsert — MUST VERIFY before merge.** OpenCR upserts on the source-key within
  one clientid (the ETL PUTs each cycle yet keeps one `consolidated` source). Whether a
  `clientid=openmrs` PUT updates the **same** source (vs. creating a second) is unconfirmed. If it does
  not, also add a deterministic match rule on the source-key in OpenCR, or route both feeds through one
  clientid.

## Changes — `openmrs-module-mpi-client`

### 1) Config (`MpiClientConfiguration`)
Add two properties + getters (pattern matches existing `PROP_NAME_*`):

```java
public static final String PROP_NAME_MSPP_CODE       = "mpi-client.source.mspp";        // per-site, no default
public static final String PROP_NAME_SOURCE_KEY_SYS  = "mpi-client.source.keySystem";   // default below

public String getMsppCode() {
    return this.getGlobalProperty(PROP_NAME_MSPP_CODE, "");
}
public String getSourceKeySystem() {
    return this.getOrCreateGlobalProperty(PROP_NAME_SOURCE_KEY_SYS,
        "http://sedish-haiti.org/fhir/source-key");
}
```

### 2) Emit the source-key + upsert (`FhirMpiClientServiceImpl.exportPatient`)
Today (≈ line 564 / 612):
```java
admitMessage = patientTranslator.toFhirResource(patientExport.getPatient());
...
MethodOutcome result = client.create().resource(admitMessage).execute();
```
Change to add the source-key identifier and switch to a **conditional update** on it:
```java
String mspp = m_configuration.getMsppCode();
String sourceKey = null;
if (mspp != null && !mspp.isEmpty() && patientExport.getPatient().getPatientId() != null) {
    sourceKey = mspp + "-" + patientExport.getPatient().getPatientId();
    admitMessage.addIdentifier()
        .setSystem(m_configuration.getSourceKeySystem())
        .setValue(sourceKey);
}

MethodOutcome result;
if (sourceKey != null) {
    // Upsert on the SEDISH source-key so the batch (consolidated) and real-time feeds converge
    // to a single source record regardless of which arrives first/last.
    result = client.update().resource(admitMessage)
        .conditional()
        .where(org.hl7.fhir.r4.model.Patient.IDENTIFIER.exactly()
            .systemAndIdentifier(m_configuration.getSourceKeySystem(), sourceKey))
        .execute();
} else {
    // No mspp configured → preserve existing behaviour.
    result = client.create().resource(admitMessage).execute();
}
```

### 3) Notes
- Apply the same source-key identifier in `updatePatient` for consistency (it already uses
  `client.update()`).
- The identifier value must match the ETL **exactly**: `mspp_code` + `-` + decimal `patient_id`
  (no zero-padding — the ETL does `CONCAT(mspp_code,'-',CAST(patient_id AS CHAR))`).

## Per-site config (after deploy)
Set on each instance (44 / 54 / 18 / …), same way as the other `mpi-client.*` props:
```
mpi-client.source.mspp        = <the site's MSPP code, e.g. 75101>
mpi-client.source.keySystem   = http://sedish-haiti.org/fhir/source-key   (auto-created default)
```
`mspp` is the only per-site value; it must equal the `mspp_code` the consolidated pipeline uses for
that facility.

## Test plan
1. **Unit:** with `mspp` set, exportPatient adds the source-key identifier and issues a conditional
   update; with `mspp` empty, behaviour is unchanged (create).
2. **Controlled OpenCR test (verifies prereq #2):** for a test source-key, PUT as `clientid=openmrs`
   then as `clientid=consolidated` (and vice-versa); confirm OpenCR holds **one** source, not two.
3. **End-to-end:** register on a site (real-time) and let the consolidated ETL push the same record;
   confirm OpenCR shows a single source under the golden (no `openmrs`/`consolidated` duplicate).

## Rollout order
1. Verify prereq #2 (controlled OpenCR test).
2. If it holds → implement the above, build the omod, set `mpi-client.source.mspp` per site.
3. If it does not → add a source-key deterministic match/upsert rule in OpenCR (or unify clientid),
   then implement the client change.
