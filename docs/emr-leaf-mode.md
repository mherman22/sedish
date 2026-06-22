*iSantePlus → "leaf mode" (consolidated-server architecture)*

*The model*
EMRs no longer talk to the HIE directly. The only path up is CDC:
`iSantePlus → (CHARESS binlog CDC) → consolidated_db → SQLMesh pipeline + FHIR-router → OpenCR (identity) + SHR (clinical)`
The EMR just writes locally and the pipeline propagates it. The only thing the EMR still does with the HIE is *read* — patient search (PDQ) at registration.

*What we turned OFF — EMR→HIE writes (no longer needed)*
Emptied in `post-start.sh` on every boot, so nothing is pushed directly:
- `mpi-client.endpoint.cr.addr` — patient PIX feed to OpenCR (on save)
- `mpi-client.endpoint.pix.addr` — PIX write-back to OpenCR (on import)
- `xdssender.exportCcdEndpoint` — clinical (CCD) push to SHR
(Also removed the old lines that pointed these at OpenHIM.)

*What we kept ON*
- `mpi-client.endpoint.pdq.addr` + per-facility credentials — read-only patient search.

*Key decisions*
- EMR is a pure data source: ingestion is CDC-only, no real-time EMR→OpenCR/SHR feed.
- `xds-sender` used to *throw* when its endpoint was blank, so we patched it upstream (IsantePlus PR #111) to skip cleanly, cut release `v2.6.1`, and bundled it.
- Keep in-EMR search — clinicians still look patients up in the registry.

*Done ✅*
- `post-start.sh` wired to leaf-mode
- `xds-sender 2.6.1` bundled into the EMR image
- Architecture written up

*Pending*
- 🔌 *CDC → consolidated_db for our EMRs* (CHARESS side) — the missing link for a true end-to-end test: create a patient in the EMR → CDC → it shows up in OpenCR + SHR via the pipeline. The downstream half (consolidated_db → OpenCR/SHR) is built and verified.
- 🚀 *Rebuild + redeploy* the iSantePlus image so the facilities boot into leaf-mode.
- 🐛 *Import duplicates* — importing a patient from OpenCR creates two local records (the module imports the patient *and* its golden). Needs a fix.
- 🧹 *OpenCR cleanup* — remove orphan/duplicate golden records, and stop full re-pushes that keep churning new ones.
