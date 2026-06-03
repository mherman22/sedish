# Resilience: Power & Connectivity in Haiti

Working notes on how the SEDISH HIE behaves under the conditions it actually
operates in — multi-day to multi-week power outages and intermittent Internet
at facilities, at the central node, and at the integration partners
(M2Sys / National Fingerprint, Consolidé).

This is a starting point for an architecture conversation, not a delivery plan.

---

## 1. The architecture we're reasoning about

From the roaming-care whiteboard:

```
              ┌────────────────────────┐                ┌───────────────────────┐
              │ National Fingerprint   │                │ Consolidé             │
              │   M2Sys (BO + WS)      │                │  ├─ Cons. Fingerprint │
              └─────────┬──────────────┘                │  └─ Cons. Medical Data│
                        │ Script.py                     └───────────┬───────────┘
                  Internet                                          │
                        │                                     Script.py
   ┌────────────────────┼─────────────────────────────────────────┐ │
   │ SEDISH             │                                         │ │
   │  ├─ MPI (OpenCR)   │                                         │ │
   │  ├─ SHR (HAPI)     │                                         │ │
   │  └─ OpenHIM  ◄─────┴──────────── Internet ─────────────────► │ │
   └─────────┬──────────────────────────────────────────────────────┘
             │ Internet
   ┌─────────┼───────────────┬───────────────┐               ┌──── Internet ────┐
   ▼         ▼               ▼               ▼               │                  │
 iSantePlus 1   iSantePlus 2  iSantePlus 3  …               (Consolidé also pulls
   HUEH           HUP           HFSCI                         directly from sites)
 (facility)     (facility)    (facility)
```

Key observations:

- SEDISH sits on **three** of the four Internet hops in the picture. It is the
  single most contended dependency.
- M2Sys is a **live** dependency for biometric matching at patient registration.
  When the Internet path SEDISH→M2Sys is down, Rule 1 (biometric exact) in
  OpenCR's [`decisionRules.json`](../packages/client-registry-opencr/config/decisionRules.json)
  cannot fire even between two facilities that are themselves online.
- The `Script.py` boxes between SEDISH↔M2Sys and SEDISH↔Consolidé look like
  batch/pull jobs. Naturally tolerant of *short* outages; we have not
  validated multi-week backlog behaviour.
- Consolidé has its own Internet path to facilities — a parallel ingestion
  channel, not a downstream of SEDISH.

---

## 2. Failure modes, by what actually breaks

| Failure | Clinical impact at the facility | Roaming care | Notes |
|---|---|---|---|
| **One facility loses power** | Total — no system runs | New visits at this facility don't propagate until power returns | Out of scope architecturally; operational (UPS/solar) |
| **One facility loses Internet, has power** | None — iSantePlus is local | Recent visits at this facility don't reach SEDISH until reconnect | mpi-client + xds-sender background queues are the recovery path. Have not been stress-tested for weeks-long retention |
| **SEDISH offline (VPS down or net loss)** | None | Breaks **everywhere**: no MPI lookups, no SHR writes, no M2Sys or Consolidé bridge | Highest blast radius. Single-region today |
| **M2Sys unreachable** | None | Biometric dedup (Rule 1) fails. Demographic / Code National rules (2–10) still match | Mitigatable by caching at SEDISH |
| **Consolidé unreachable** | None | None for live care; affects national reporting / analytics | Lowest priority |

The pattern that emerges: SEDISH and M2Sys reachability are the high-leverage
items. Individual facility outages are forward-recoverable as long as the
local queues hold.

---

## 3. Recommendations, prioritised

### Tier 1 — Harden what we have (days to a couple of weeks of work)

These are cheap and address the most common failure shapes.

1. **Cache M2Sys at SEDISH.** Mirror fingerprint templates into an
   OpenCR-adjacent store so SEDISH can answer "do we know this fingerprint?"
   locally. M2Sys becomes an authoritative *sync target*, not a live
   dependency. Today, Rule 1 dies the moment the M2Sys link is down.
2. **Stress-test the OMOD queues for multi-week outages.** Specifically:
   - [`openmrs-module-mpi-client`](https://github.com/IsantePlus/openmrs-module-mpi-client) `PatientSyncWorker` — does the queue survive iSantePlus restarts? Exponential backoff?
   - [`openmrs-module-xds-sender`](https://github.com/IsantePlus/openmrs-module-xds-sender) `PullNotificationsTask` — same questions, plus: does a two-week backlog replay cleanly without duplicates? Recent SSL fix in [#110](https://github.com/IsantePlus/openmrs-module-xds-sender/pull/110) addressed *connection* failure, not *retention*.
3. **Same audit for the Script.py jobs** between SEDISH↔M2Sys and
   SEDISH↔Consolidé. Idempotent? Persistent cursor? Rate-limited replay so a
   reconnecting facility doesn't overwhelm SEDISH or M2Sys?
4. **Operational, not architectural, but real:** UPS + solar at facilities,
   multi-SIM LTE fallback at SEDISH. Most "outages" in practice are hours-long,
   not weeks-long; this handles 90% of them.
5. **Queue-depth monitoring** on every queue named above. Today we'd find out
   about a backlog when someone notices missing data.

### Tier 2 — Central HA (weeks of work)

Address the single biggest blast-radius failure: SEDISH itself going down.

- Two-region deployment of the SEDISH stack (OpenHIM, HAPI/SHR, OpenCR).
- Postgres streaming replication or managed-DB equivalent.
- OpenCR mirror — patient identity is the read-heavy path; a stale-but-present
  replica is far better than nothing.
- DNS-level or LB-level failover, manual cutover acceptable for v1.

This does *not* help an offline facility, but it removes "central SEDISH is a
SPOF" from the risk register, which is currently the most expensive failure
to recover from.

### Tier 3 — Edge HIE per facility (months, real infra lift)

Only worth doing if the requirement is: "patient from offline Facility A walks
into Facility B *during* the outage and B can see A's recent history."

Shape:
- Lightweight stack at each site: mini OpenCR + HAPI cache + local OpenHIM.
- Facility reads local first, falls back to central.
- Bidirectional reconciliation on reconnect.

Costs:
- Another stack to maintain in places where infra is already the problem.
- Identity reconciliation across edge + central is non-trivial — exactly the
  golden-record normalisation problem that
  [DIGI-UW/shared-health-record#131](https://github.com/DIGI-UW/shared-health-record/pull/131)
  just solved centrally, now distributed.

Defer this until Tier 1 + 2 are done and we have evidence the "live offline
roaming" case is actually common enough to justify it.

---

## 4. Open questions to answer before committing

1. How often, realistically, is each Internet hop down, and for how long?
   We're guessing. Anecdotal data from sites would change the priority order.
2. Are the Script.py jobs scheduled (cron) or event-driven? Where do they run?
3. What's the disaster-recovery story for the SEDISH VPS today? Snapshots?
   Backups? Restore time?
4. Does M2Sys expose a "sync since" API, or do we have to scrape it whole each
   time? Determines what "cache M2Sys at SEDISH" actually costs.
5. Is there an existing pattern in IsantePlus for "offline-capable" mode we'd
   be aligning with, or are we inventing one?

---

## 5. What this doc is *not*

- A design doc. Each Tier 1 item probably needs its own short design before
  implementation.
- A commitment to do all of this. Tier 3 in particular may never be worth it.
- A statement about current functionality. Roaming care works today under
  normal connectivity — see the
  [recent cleanup work](../README.md) and the merged PRs in `charess-org/sedish`
  ([#1](https://github.com/charess-org/sedish/pull/1),
  [#2](https://github.com/charess-org/sedish/pull/2),
  [#3](https://github.com/charess-org/sedish/pull/3)).
  This doc is about what happens when conditions stop being normal.
