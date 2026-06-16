# SEDISH `hie` stack

The SQLMesh → FHIR pipeline (`consolidated-fhir-mapper`) — the path from the **external
Consolidé** consolidated server to SEDISH's MPI (OpenCR) and, in Phase 2, the SHR.

```
Consolidé (EXTERNAL, CHARESS-hosted MySQL: consolidated_db)
   │   (SQLMesh reads it; CDC into Consolidé is CHARESS's responsibility, not ours)
   ▼
 fhir-pipeline
   ├─ sqlmesh run  → fhir.patient (+ encounter/observation/… in Phase 2)
   └─ loader       → OpenCR /CR (identity / MPI).  Phase 2 also: SHR /SHR (clinical)
```

## Architecture: Consolidé is external
The consolidated server ("Consolidé") is **owned and hosted by CHARESS** — they give us the
MySQL connection details. We do **not** run a local consolidated DB or a CDC reader; getting
EMR data into `consolidated_db` is their side of the boundary.

SQLMesh runs **against Consolidé's MySQL**: it reads `consolidated_db` and writes its own
`fhir` / `ref` / state schemas there. So it needs **CREATE/write privileges** on that server
(source and output must live on one server — MySQL can't JOIN across servers). If CHARESS
grants only read-only access, point `CONSOLIDATED_HOST` at a **local replica** of
`consolidated_db` instead — that's the one open question for their access model.

## Components
- **`projects/consolidated-fhir-mapper/`** — git submodule (the authoritative repo). SQLMesh
  models map `consolidated_db` → FHIR; the loader pushes the delta to OpenHIM. Image is built
  locally by `deploy.sh`.
- **`hie/`** — this orchestration: the single-service stack + `deploy.sh` + `.env`.

## Service (1)
| Service | Role |
|---|---|
| `fhir-pipeline` | SQLMesh transform + loader. `RUN_MODE=poll` (timer, default) or `kafka` if Consolidé exposes a patient-changed event stream. Phase 1: `MPI_ONLY=1` → Patient → OpenCR only. |

## Deploy
```bash
git clone --recurse-submodules <sedish>        # or: git submodule update --init
cd hie
cp .env.example .env        # REQUIRED: set CONSOLIDATED_HOST/USER/PASS (the Consolidé MySQL)
./deploy.sh                 # builds the image, then docker stack deploy hie
```
Prereqs: the `openhim_public` overlay exists, OpenHIM channel `/CR/fhir` is configured, and
the pipeline can reach the Consolidé MySQL from the swarm.

## Verify
```bash
docker service logs -f hie_fhir-pipeline
# landed in the MPI: cross-facility patients sharing a national id -> one golden record
curl -su openshr:openshr 'http://openhim-core:5001/CR/fhir/Patient?identifier=<national-id>'
```

## Teardown
```bash
docker stack rm hie
```
