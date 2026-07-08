# Returning Client Registry results to the EMR — design note

**Audience:** CHARESS + DIGI/HIE team.
**Scope now:** demographics only. **Designed so** the same mechanism later returns clinical data
(from the SHR) with no re-architecture.

Based on the CHARESS summary: *at registration (online), OpenCR receives demographics (+ fingerprint
if any), deduplicates, and returns a **Golden ID** stored on the EMR; at search time, OpenCR returns
every occurrence of the patient across sites, later accompanied by clinical data.*

---

## 1. The two flows

**Phase 1 — Registration (write): dedup → return Golden ID → store on EMR**
```
EMR save ──demographics (+ fingerprint)──► OpenHIM ──► OpenCR
   OpenCR deduplicates → matches an existing golden OR creates a new one
   EMR resolves the Golden ID ◄────────────────────────────
   EMR stores it locally as the ECID identifier (patient_identifier)
```

**Phase 2 — Search / roaming (read): Golden ID → all occurrences (+ later, clinical)**
```
EMR ──Golden ID──► OpenHIM
   OpenCR → golden → every linked source record (occurrence) across sites
   (+ later) OpenHIM pulls clinical from the SHR, keyed by the golden
   EMR renders one record ◄──────────────────────────────
```

---

## 2. The core idea: one Golden-ID-keyed envelope, extensible demographics → clinical

Both phases return the **same shape** — a FHIR **Bundle keyed by the Golden ID** — whose sections
are filled in over time. This is the existing consolidated-IPS envelope (`/SHR/ips`); today it carries
demographics, later the same bundle carries SHR clinical. **No new mechanism is needed when clinical
arrives — only new sections.**

```
Bundle (type: document)
 └─ Composition  (subject = golden Patient; title "Person Registry Summary")
     ├─ section: Identity           → Golden ID (CRUID/ECID) + golden Patient (merged demographics)   [NOW]
     ├─ section: Occurrences        → one entry per site source record:                                [NOW]
     │                                 site, local identifiers, demographics, match score
     └─ section: Clinical           → empty placeholder                                                 [LATER: from SHR]
 └─ Patient (golden)                                                                                    [NOW]
 └─ Patient (source @ Site A), Patient (source @ Site B), …                                             [NOW]
 └─ Condition / Observation / … (from SHR)                                                              [LATER]
```

The EMR consumes one stable envelope; adding clinical is additive.

---

## 3. Endpoints (what we expose)

**Phase 1 — resolve the Golden ID (registration).** After the PIX feed, the EMR resolves the golden:
```
GET /CR/fhir/Patient?identifier=<localSystem>|<localValue>&_include=Patient:link
→ pick the entry tagged golden (5c827da5-…) ; follow link.type=replaced-by to the survivor
→ golden.id = Golden ID  →  store on the patient as ECID
```
Minimal registration response the EMR needs: `{ goldenId, dedupStatus: new|matched|potential, occurrences[] }`.

**Phase 2 — retrieve the person record (search / after-save display).** Demographics-only now:
```
GET /SHR/ips/Patient/cruid/<goldenId>?scope=demographics
→ Golden-ID-keyed bundle: Identity + Occurrences sections (golden + all source records), Clinical empty
```
Later, `scope=full` (or default) adds the Clinical section from the SHR — same endpoint, same bundle.

> Both entry points (registration and roaming search) call the **same resolution + bundle assembly**;
> the after-save page is just Phase 2 invoked for the patient just registered.

---

## 4. The after-save page (what the clinician sees)
Renders the Phase-2 bundle for the just-registered patient — mirroring the CR's view of the person:
1. **Golden ID / ECID** (the enterprise identity),
2. **Demographics** (the golden's merged view),
3. **Occurrences across sites** (name, DOB, sex, site, local IDs, match score),
4. **Clinical** — a visible placeholder ("à venir depuis le SHR") so the slot exists from day one.

---

## 5. Decisions locked (to stop the circular discussion)
1. **The "Golden ID" is the OpenCR golden record's id (UUID)**, stored on the EMR as the **ECID**
   identifier. (A *friendly* national number would be a separate OpenCR id-generation change.)
2. **The EMR gets it via explicit resolve** (query → golden → `replaced-by` follow → store as ECID),
   rather than native PIX cross-reference — we control it and it self-heals on golden merges.
3. **The Golden ID is a person key for *retrieval*, not for finding the patient in the CR** — the local
   identifier already resolves the golden. You **store** it at registration and **use** it to pull the
   person's record (demographics now, clinical later) as one golden-keyed bundle.
4. **The stored ECID is a cache, not a source of truth** — always re-resolve (follow `replaced-by`)
   on use, because golden merges can move it.

---

## 6. Exists vs. new
- **Exists:** PIX feed of demographics + fingerprint (mpi-client); golden resolution + occurrences
  (the `/SHR/ips` resolver, `registrationCoreFindSimilar`); the IPS bundle envelope.
- **New (small):** (a) store the resolved Golden ID as ECID at registration; (b) a
  `scope=demographics` variant of the bundle (occurrences, no clinical) for now; (c) the after-save
  page that renders the bundle, hooked into the existing post-save summary slot.

---

## 7. One-line framing for CHARESS
> The Golden ID is the **deduplicated person key**: OpenCR returns it at registration (we store it as
> ECID), and we use it to retrieve the person's full cross-site record as **one Golden-ID-keyed
> bundle** — demographics today, the same bundle carrying SHR clinical tomorrow.
