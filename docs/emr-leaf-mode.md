Update on connecting iSantePlus to the SHR and OpenCR.

The integration is split by data type. **Demographics/identity** are pushed by the EMR directly to
the Client Registry (OpenCR) in real time — the EMR's `mpi-client` PIX feed, which fires on patient
save when the site has internet — plus normal patient search (PDQ). **Clinical data** is *not*
pushed by the EMR; it flows through the consolidated server: EMR → CHARESS binlog CDC →
`consolidated_db` → our SQLMesh pipeline + FHIR-router mediator → the SHR.

Concretely, on each iSantePlus instance (`post-start.sh`): the `mpi-client` endpoints
(`cr.addr`/`pix.addr`/`pdq.addr`) point at `/CR/fhir` so demographics and search work, and the
`xds-sender` CCD push to the SHR (`exportCcdEndpoint`) is left off — clinical only reaches the HIE
through the consolidated route. `xds-sender` 2.6.1 (with the upstream fix) is bundled so that
disabled endpoint skips cleanly.

Open points still to confirm with the team / CHARESS:

- **Offline resilience** — the real-time demographics feed needs store-and-forward / retry when a
  site reconnects (this is what the `outgoing-message-exceptions` module is for; confirm it covers
  the mpi-client feed).
- **Does the consolidated server carry demographics too, or clinical only?** If clinical only, there
  is no bulk fallback for identity, so an offline site's clinical can reach the SHR before its
  demographics reach OpenCR. If it carries demographics, the pipeline can also reconcile identity in
  bulk (the dual real-time + bulk feed the spec describes).
- **Deduplication** — with the EMR (real-time) and possibly the pipeline (bulk) both feeding OpenCR,
  the decision rules + consistent identifiers (biometric, code-national, source-key) must reliably
  collapse them into one golden record.
