# OpenCR dual-feed convergence — design note

**Audience:** CHARESS + DIGI/HIE team.
**Status:** decision needed. One half (identifier mislabeling) is already fixed; the other half
(one-source-vs-two) is a policy choice described below.

## The situation

The same patient can reach OpenCR by **two independent paths**:

1. **Site real-time feed** — the EMR's `santedb-mpiclient` posts each patient on save
   (`clientid = openmrs`). Uses the fhir2 identifier systems from the site's
   `fhir_patient_identifier_system` (e.g. `…/3-isanteplus-id`, `…/5-code-national`).
2. **Consolidated batch feed** — the consolidated→FHIR ETL (`sedish-fhir-pipeline`) polls the
   consolidated DB and pushes to OpenCR (`clientid = consolidated`). Carries the **SEDISH source-key**
   (`mspp_code-patient_id`, system `http://sedish-haiti.org/fhir/source-key`) and does a
   conditional-update upsert on it.

This is intentional per the CHARESS identity spec ("dual real-time + bulk feed into OpenCR; the ETL is
a **batch/reconciler**, not the sole writer"). See [[charess_identity_spec]],
[`cr-return-to-emr-design.md`](cr-return-to-emr-design.md), [`emr-cutover-to-consolidated.md`](emr-cutover-to-consolidated.md).

## What we observed (patient "user_stanne test")

OpenCR held **6 source records → 1 golden** — i.e. 3 registrations, each ingested **twice** (once per
feed). Two defects were behind it:

1. **Mislabeled identifiers (fixed).** The ETL mapped identifier→system by the *numeric*
   `patient_identifier_type` id, which isn't stable across EMRs, so an iSantePlus ID came through as
   Code PC (`…/9-code-pc`) and a Code National as Code ST (`…/4-code-st`). With different systems than
   the site feed, OpenCR couldn't match them deterministically and fell back to demographic matching.
   → **Fixed** in `sedish-fhir-pipeline` PR #30 (map by type **name**; align Code ST to `…/6-code-st`).

2. **No shared source key across feeds (open).** Even with correct systems, the two records come from
   two different `clientid`s and share no upsert key, so OpenCR keeps them as **two source records**
   (linked to one golden). The ETL upserts on the SEDISH source-key, but **the real-time feed does not
   emit that key**, so the "converge to one source" mechanism never engages.

Evidence (same person, two sources):

| | Site feed (`clientid=openmrs`) | Consolidated feed (`clientid=consolidated`) |
|---|---|---|
| `10013N` | `…/3-isanteplus-id` | `…/9-code-pc` ❌ → fixed to `…/3-isanteplus-id` |
| `TU1010M` | `…/5-code-national` | `…/4-code-st` ❌ → fixed to `…/5-code-national` |
| source key | *(none)* | `source-key = 75101-37` |

## The decision: one source per person, or two-under-one-golden?

**Option A — Accept two sources → one golden (lowest effort).**
Per the spec, dual-feed is expected and OpenCR's job is to resolve identity into one golden — which it
does. With PR #30, the two feeds now carry matching identifiers, so the golden is clean and correctly
labeled. De-duplicate at **read time** (e.g. `getGoldenRecordOccurrences` collapses occurrences that
share the SEDISH source-key or the same local id) so the UI shows one row per real record.
*Pro:* no new write-path work. *Con:* OpenCR physically stores two source records per person.

**Option B — Single feed per site (REJECTED — see requirement below).**
A site that is on the real-time feed is **excluded from the ETL push** (and vice-versa). One source
per registration, no reconciliation needed. This is the cutover posture in
[`emr-cutover-to-consolidated.md`](emr-cutover-to-consolidated.md).
*Pro:* exactly one source per person. *Con:* requires the ETL to filter out real-time sites, and loses
the "reconciler" safety net for those sites.

**Option C — Shared source-key on both feeds (true convergence).**
Make the real-time feed also emit and **upsert on** the SEDISH source-key
(`PUT /Patient?identifier=source-key|<mspp_code-patient_id>`), same as the ETL. Then OpenCR updates a
single source regardless of which path wrote last.
*Pro:* both feeds converge to one source; keeps dual-feed resilience. *Con:* the `mpi-client` doesn't
know `mspp_code` or the consolidated `patient_id`, so it can't build the source-key today — needs a
way to derive/carry it (config for `mspp_code` + a rule for the local id). Non-trivial.

## Hard requirement: every site on BOTH feeds

CHARESS requirement: **all sites run both feeds simultaneously.** The real-time feed carries the
patient when the site has internet/power; the consolidated batch feed is the **catch-up path** for
when it doesn't (offline registration is delivered later when the consolidated server next syncs).
Neither path can be dropped per site — so **Option B is rejected**.

Given both feeds always run, the normal (online) case produces the duplicate every time, so
convergence is mandatory: **Option C is the target.**

## Recommendation

- **Now:** merge PR #30 (identifier mislabeling) — strictly correct and a prerequisite for C (both
  feeds must use the same systems before any key-based merge is meaningful).
- **Then implement Option C** — the real-time feed must also carry and **upsert on** the SEDISH
  source-key, so the two feeds resolve to one source record regardless of which arrives first/last.

### What Option C requires
1. **Real-time feed emits the source-key.** `santedb-mpiclient` must add an identifier
   `system = http://sedish-haiti.org/fhir/source-key`, `value = <mspp_code>-<patient_id>` — the *same*
   value the ETL builds. The site must be configured with its **`mspp_code`**, and use the OpenMRS
   **`patient_id`** as the second part.
2. **Upsert, not create.** The feed must do a FHIR conditional update
   (`PUT /Patient?identifier=source-key|<value>`) instead of a blind create, so the second feed updates
   the first's record. The ETL already does this; `mpi-client` currently does a `create()` (POST) and
   would need to change.

### Two things to confirm before building C
- **patient_id parity** — the ETL's source-key uses the consolidated DB's `patient_id`; the real-time
  feed would use the site's OpenMRS `patient.patient_id`. These must be the **same number** for the
  keys to match. Confirm the consolidated DB preserves the source EMR's `patient_id` (per the CHARESS
  spec it is the idempotency key, so it should — but verify against a real record).
- **OpenCR upsert-on-identifier** — confirm OpenCR honors FHIR conditional update by the source-key
  (updates the existing source rather than creating a second) **across different `clientid`s**. If it
  does not, C needs an OpenCR matching/config change (treat source-key as a deterministic identity),
  not just a client change.

Until C ships, **Option A** is the interim state (two sources → one golden, identifiers now correct
after PR #30) — acceptable for identity, but the physical duplicate remains.

## Cross-references
- Identifier fix: `sedish-fhir-pipeline` PR #30 (map by type name).
- Site identifier seeding + real-time setup: [`charess-emr-realtime-mpi-setup.md`](charess-emr-realtime-mpi-setup.md).
- Return-to-EMR / golden id: [`cr-return-to-emr-design.md`](cr-return-to-emr-design.md),
  [`registration-result-page.md`](registration-result-page.md).
- Cutover posture: [`emr-cutover-to-consolidated.md`](emr-cutover-to-consolidated.md).
