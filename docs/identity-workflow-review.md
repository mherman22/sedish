# Identity & retrieval workflow — review and rethink (2026-07, re-measured 2026-08)

**Audience:** CHARESS + DIGI/HIE team.
**Status:** review + decisions needed. Consolidates what we built and *measured* in July 2026 against
the original design, and lists the open choices. **D1 has since been implemented and verified** —
see §2b for the August re-measurement.

Builds on (does not replace): [`cr-return-to-emr-design.md`](cr-return-to-emr-design.md),
[`opencr-dual-feed-convergence.md`](opencr-dual-feed-convergence.md),
[`mpi-client-source-key-spec.md`](mpi-client-source-key-spec.md),
[`registration-result-page.md`](registration-result-page.md), [`ips-pull-flow.md`](ips-pull-flow.md).

---

## TL;DR

The core works and is sound: **source-key (`<mspp_code>-<patient_id>`) as the identity key**,
cross-site linking in OpenCR, and the consolidated IPS. But the identity path is handled in several
half-coordinated places and **nothing reconciles the two stores (MPI ↔ SHR)**. Four things need a
decision, safety first:

1. **Matching can merge different people** (biometric-only auto-link + shared biometric codes) — a
   patient-safety issue, because the consolidated IPS then shows a stranger's clinical data.
   → **Fixed in `ede3760`; verified clean on 2026-08-02** (§2b). A second, *identifier-driven*
   false-merge path remains (malformed source-keys) — now the top safety item.
2. **MPI ↔ SHR drift** — measured **29 patients** with clinical data in the SHR but **no resolvable
   golden in the MPI** → the IPS silently 404s. 25 of them are on the demo sites (44, 54).
   → **now 11** (2026-08-02), but nothing reconciles the stores, so it re-accumulates.
3. **The ECID / "golden returned to the EMR" mechanism is decorative** — nothing authoritative reads
   it; the system resolves the golden **live by source-key** every time.
4. **Retrieval is MPI-only** — no SHR fallback, so a store mismatch means "nothing found" instead of
   "at least this site's record."

---

## 1. Intended design vs. as-built

**Intended** (per [`cr-return-to-emr-design.md`](cr-return-to-emr-design.md)):

- *Write:* EMR save → OpenCR dedups → **returns the Golden ID** → EMR **stores it as the ECID**.
- *Read:* EMR uses the stored **Golden ID** → OpenCR returns every occurrence (names, identifiers) →
  later, the same Golden-ID-keyed `/SHR/ips` envelope carries clinical.

**As-built** (verified this session):

- OpenCR does **not** hand the golden back on save. The mpi-client **re-queries by source-key**
  (`Patient?identifier=source-key|<key>&_include=Patient:link`) to discover the golden, then stores
  it as the ECID — best-effort, with a first-save timing gap.
- Retrieval is keyed on the **source-key**, not the stored Golden ID: the EMR ips module fetches
  `/SHR/ips/Patient/source-key/<key>`, the mediator resolves the golden **live** from the source-key,
  then assembles the consolidated IPS. The stored **ECID is read in exactly one place** — the
  registration-result page's offline display fallback.

**Consequence:** the source-key is the real identity key end-to-end; the Golden-ID-in-the-EMR
mechanism from the original design was superseded (for good reasons — see §3.3) and is now vestigial.

Concrete example (patient `b897cfe2…` = "Leo Harry", the one in the sample URL):

```
GOLDEN 5bd9eabe   name=[]  ids=[]   → seealso b897cfe2 (site 73106), 42b3197f (site 54111)
 source b897cfe2  site 73106  "Leo Harry"  source-key=73106-52, national=HL0726V, isanteplus=1000PE …
 source 42b3197f  site 54111  "Leo Harry"  source-key=54111-25, national=HL0726V, isanteplus=1000PE …
```
The golden carries **no** demographics/identifiers; they live on the sources. Cross-site linking and
"find all occurrences" both work — via live source-key resolution.

## 2. Evidence gathered (July 2026)

- **Store divergence.** 631 source-keys in the MPI, 631 in the SHR, **602 overlap**. **29 in the SHR
  but not the MPI** → IPS 404s despite clinical data (11 on site 73106/44, 14 on 75101/54, 4 on
  54111). **29 in the MPI but not the SHR** → IPS resolves but has no clinical to gather.
- **Shared biometrics.** ~6 biometric reference codes are shared across **distinct** people (e.g.
  `HT-90000434` on two different patients). OpenCR decision **rule 1 auto-links on biometric match
  alone**, so these become one golden fusing two people. See [`identifier-mapping`](#) history.
- **Site codes (from live MPI/SHR tags):** 44 = `73106`, 54 = `75101` (not `73104`), 18 = `73105`
  (no records in either store yet).
- **patient_id parity confirmed** (the linchpin of the source-key): the consolidated DB preserves the
  site's OpenMRS `patient.patient_id`, so the batch feed's `mspp-patient_id` equals the real-time
  feed's `patient.getPatientId()`-derived key (corroborated: iSante ID `751010037` ↔ `75101-37`).
- **Concept-dimension gap (fixed):** 161 observations referenced concepts absent from the
  consolidated `concept` table (`concept_id 0` ×160 + `509167110`), rendering as raw padded CIEL
  UUIDs in the IPS. Filtered in the pipeline (`sedish-fhir-pipeline` PR #38) and the 161 retracted.

## 2b. August 2026 re-measurement (after the rule-1 fix)

Re-run against the live stack on 2026-08-02, after `ede3760` (biometric rule now requires name
corroboration; per-facility id systems dropped from the `internalid` upsert handles) and `39315e2`
(pinned `itechuw/opencr:develop` + `itechuw/elasticsearch-opencr:develop`).

**Store shape.** MPI 1362 Patients = 679 golden + 683 source. SHR 1200 = 540 golden shells + 660
source. 685 distinct source-keys in the MPI, 660 in the SHR.

**D1 is fixed and did not over-tighten.** Matching still links across sites — 77 goldens link >1
source, 72 of them cross-site (fan-out 1×512, 2×61, 3×10, 4×3, 5×3). The 5 *same-site* merges were
inspected individually and are all **true duplicates** (identical name + birthDate + sex, adjacent
`patient_id`s — the same person registered twice at one facility), e.g. `75101-7`/`75101-8` "Fast
Super" and `11002-1260`/`11002-1261` "Wadson Pétion". **No false merge of distinct people was found.**

**Drift shrank but did not close.** overlap **649**, **SHR-only 11**, **MPI-only 36** (was 602 / 29 /
29 in July). SHR-only by site: 73106×7, 75101×3, 54111×1 — these are the records that still 404 the
IPS. §3.2 and D2 stand: nothing reconciles the two stores, so this drifts again on its own.

**Three new findings, none patient-safety-critical:**

1. **90 orphan goldens** (13% of goldens) carry *zero* `seealso` links. 17 are mirrored into the SHR.
   Checked all 17 for `Observation|Condition|MedicationRequest|Encounter|AllergyIntolerance|
   Immunization` — **0 clinical resources**. So these are harmless shells, not stranded data; still
   worth garbage-collecting so golden counts mean something.
2. **21 source records carry the source-key identifier twice.** Their tags show *both*
   `clientid: consolidated` and `clientid: openmrs` — so the dual feed **does** converge onto one
   source record (Option C works, answering half of D2's MUST-VERIFY), but each feed appends its own
   copy of the identifier instead of deduping. One copy is fully typed ("SEDISH Source Key"), the
   other is a bare `{system, value}`.
3. **9 records carry a malformed source-key** — two distinct causes:
   - 7 × site prefix `3111411111` (a 10-digit non-MSPP code), all `clientid: openmrs`, no
     `mspp-site` tag, and their other identifier has an **empty system**. Their values (`3111428`,
     `3111448`) show the real site is `31114`, so the key should be `31114-28`. An EMR was
     registering with a bad/placeholder `mspp_code`.
   - 2 × a hyphen-less key built from the iSantePlus ID (`541110036` = `54111` + `0036`), sitting on
     a record that *also* holds a well-formed pipeline key for a **different site** (`73105-36`),
     with an `mspp-site` tag for a **third** site (`75101`). Two different source identities have
     been fused onto one resource. This is the remaining false-merge class — identifier-driven, not
     biometric — and it is the same failure mode as `identifier-mapping-investigation`.

**Implication for the decisions below:** D1 can be closed. D2 gains evidence (convergence works;
reconciliation still missing) and a new sub-item: reject malformed source-keys at write time.

## 3. The four problems (root → symptom)

Root cause: **no single authority reconciles identity across the MPI, the SHR, and the EMR.**

### 3.1 Matching can merge different people (safety-critical)
OpenCR rule 1 = "biometric match → auto-link", and biometric codes are shared across distinct people
in the data. A false-merged golden aggregates two patients' clinical resources; the consolidated IPS
then shows a stranger's meds/labs, and the EMR-side `IpsPatientMatcher` (which validates only the
*subject* Patient) passes it. **This is the sharpest issue: it's a wrong-patient clinical hazard.**

### 3.2 MPI ↔ SHR drift (the 29)
Two feeds write patients to OpenCR (real-time mpi-client + batch pipeline) — intended per the CHARESS
spec, with the pipeline as reconciler. The convergence mechanism (both feeds carry the source-key and
upsert on it — [`mpi-client-source-key-spec.md`](mpi-client-source-key-spec.md), "Option C") is now
**coded in the vendored mpi-client** but its **deployment to the EMRs and OpenCR's cross-`clientid`
upsert behavior are unverified** (flagged MUST-VERIFY in the convergence doc). The reconciler heals
`source → SHR clinical` but **never reconciles MPI ↔ SHR patient identity**, so the 29 divergent
records are never detected or repaired.

### 3.3 The ECID / golden-return loop is a half-built third mechanism
"CR returns golden → store as ECID → drive occurrences off it" never fully worked (async timing on
first save; staleness after an OpenCR merge changes the CRUID and it is not pushed back; and
`getGoldenRecordId` originally keyed on the **colliding iSantePlus ID**). We fixed the symptoms by
resolving live from the source-key — which left the ECID decorative. The current write-back is safe
(live source-key resolution, idempotent, `SUPPRESS` prevents a re-feed loop, and export drops
UUID-valued ids so the ECID is never re-exported), but it drives nothing.

### 3.4 Retrieval is MPI-only
The mediator resolves the golden only via the MPI; if the MPI has no golden for the source-key
(the 29), it returns 404 even though the SHR holds the clinical data.

## 4. Target model

1. **Source-key is *the* identity, end to end.** One key, resolved live. Demote the ECID to an
   explicitly read-only offline display cache, or drop it — never read for logic.
2. **The reconciler owns cross-store consistency.** Extend it beyond `source → SHR` to guarantee
   **every source-key in the SHR has a linked golden in the MPI, and vice-versa** — closing the drift
   structurally instead of chasing 404s. Verify Option C is deployed and that OpenCR converges the two
   feeds to one source (cross-`clientid` upsert or a deterministic source-key match rule).
3. **Matching must be safe before it is complete.** Tighten rule 1 so a biometric match requires
   demographic corroboration; and have the IPS assembler drop clinical from linked sources whose
   demographics conflict with the golden. Prefer under-merging to a wrong-patient merge.
4. **Retrieval resolves defensively.** MPI-first, **SHR-fallback** so a site always sees at least its
   own record.

## 5. Open decisions (need CHARESS/DIGI)

- **D1 (safety). ✅ DONE** (`ede3760`). Rule 1 now requires biometric **+** name corroboration, and the
  per-facility id systems (`isanteplus-id`, `code-st`) were dropped from the `internalid` upsert
  handles. Verified in §2b: no false merge of distinct people remains, and cross-site linking is
  intact. *Residual:* CHARESS still owns cleaning the shared-biometric source data.
- **D2.** Make the batch pipeline the MPI↔SHR reconciler (add an identity-parity pass)?
  **Partly answered:** §2b confirms OpenCR *does* converge the dual feed onto one source record, so
  Option C works. Still open: (a) the parity pass itself — 11 SHR-only / 36 MPI-only keys today;
  (b) **reject malformed source-keys at write time** (the `3111411111-*` and hyphen-less
  iSante-ID-derived keys), since a bad key silently creates a second identity or fuses two;
  (c) dedupe the double-written source-key identifier.
- **D3.** Retire the ECID write-back (lean fully on source-key), or keep it as a labelled read-only
  offline cache? (No authoritative consumer today.)
- **D4.** Add the SHR-fallback route to the `/SHR/ips` mediator? Would cover today's 11 SHR-only keys.
- **D5 (new, low risk).** Garbage-collect the 90 link-less golden shells (verified to hold no
  clinical data) so golden counts are meaningful.

## 6. Priority / next steps

1. ~~**D1 — false-merge safety**~~ — **done** (`ede3760`), verified 2026-08-02.
2. **D2b — reject malformed source-keys at write time** (still a live false-merge path: 9 records,
   2 of them fusing distinct source identities). Now the sharpest remaining safety item.
3. **D2a — MPI↔SHR identity-parity pass** (closes the 11/36 divergence; stops future drift).
4. **D4 — SHR-fallback retrieval** (cheap robustness win; covers the 11 today).
5. **D3 — settle the ECID** / **D5 — GC the 90 orphan goldens** (cleanup, low risk).

None of these blocks the current demo (the happy path — 649/660 — works). D2 is now the real
production blocker.

## References
- Design intent: [`cr-return-to-emr-design.md`](cr-return-to-emr-design.md)
- Dual-feed + convergence: [`opencr-dual-feed-convergence.md`](opencr-dual-feed-convergence.md),
  [`mpi-client-source-key-spec.md`](mpi-client-source-key-spec.md)
- Retrieval: [`ips-pull-flow.md`](ips-pull-flow.md), [`registration-result-page.md`](registration-result-page.md)
- Source-key IPS route: DIGI-UW/shared-health-record PR #150 (`/Patient/source-key/:id`).
- Unnamed-concept obs filter: DIGI-UW/sedish-fhir-pipeline PR #38.
