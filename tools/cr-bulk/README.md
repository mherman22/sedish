# cr-bulk — bulk tooling for the Client Registry

One script, three jobs. They are separate because they use **different write paths**, and mixing
them is how you either miss matching or trigger it when you did not want it.

| command | path | matching runs? | use it for |
|---|---|---|---|
| `reindex` | FHIR store → Elasticsearch | **no** | index changes, stale/wiped index |
| `load` | CSV → OpenCR `/fhir` | **yes** | exercising the decision rules with a corpus |
| `verify` | reads goldens back | — | scoring a rule-test corpus |

## Why `reindex` exists

OpenCR's own startup sync (`cacheFHIR.fhir2ES`) stops after **one page of 20 records**. Measured on
this stack: 719 indexed patients, and a full resync reported `processing 1/20 … 20/20 … Done`.

So the index is maintained *only* by per-write indexing. If Elasticsearch is ever wiped or the index
definition changes, OpenCR restores 20 records of N and every rule then finds almost no candidates —
silently, because no rule errors when its candidate pool is empty. `reindex` is the way back.

It reads the field list from `PatientRelationship.json`, so the tool and the index cannot drift:
that file is the single definition of what is indexed.

## Running it

The stack's overlay networks are not attachable, so run as a one-shot swarm job:

```bash
docker service create --mode replicated-job --name crbulk-reindex --network opencr \
  --restart-condition none \
  --mount type=bind,src=$PWD/tools/cr-bulk/crbulk.py,dst=/tmp/crbulk.py,readonly \
  --mount type=bind,src=$PWD/packages/client-registry-opencr/config/PatientRelationship.json,dst=/tmp/pr.json,readonly \
  python:3.12-alpine python /tmp/crbulk.py reindex \
    --fhir http://opencr-fhir:8080/fhir --es http://opencr-es:9200 --relationship /tmp/pr.json

docker service logs crbulk-reindex     # then: docker service rm crbulk-reindex
```

Verified on the live stack: `read 1239 patients, indexed 719, skipped 520 (no name — golden shells)`.
Golden records are skipped deliberately — they carry no demographics, so indexing them would put
empty documents in the candidate pool of every query.

## Loading a rule-test corpus

`load` pushes each CSV row through OpenCR so matching actually runs, then `verify` scores the
outcome. It is built for the shape used by
[SIGDEP-3's `pims_rule_test_dataset.csv`](https://github.com/SIGDEP-3/SIGDEP-3-Docker-Setup/blob/main/test/pims_rule_test_dataset.csv):
a `Match Type` column (`Auto` / …) and a `Comment` that starts with a case id, so rows `1A` and `1B`
are one expected cluster.

```bash
python crbulk.py load corpus.csv --cr http://opencr:3000 --id-prefix rules --concurrency 8
python crbulk.py verify corpus.csv --fhir http://opencr-fhir:8080/fhir --id-prefix rules
```

`verify` reports `pass / fail / skipped` per case and exits non-zero on any failure, so it can gate
a rule change in CI. Every loaded record is id-prefixed, which is also how you clean up afterwards.

Column mapping defaults to that dataset's headers; override with `--mapping '{"last_name":"nom",…}'`.

> **Run `load` against a disposable registry, not production.** It writes real patients and forms
> real goldens. The ephemeral harness in `roaming-care-cr-test` is the right target.

## Scale

Everything streams — the CSV is read row by row, FHIR is paged, and ES writes go out in batched
`_bulk` requests (`--batch`, default 500 docs). 100k rows is a throughput question, not a memory
one. `load` is concurrent (`--concurrency`); raise it only as fast as OpenCR's matching keeps up,
since every write runs the full rule set.
