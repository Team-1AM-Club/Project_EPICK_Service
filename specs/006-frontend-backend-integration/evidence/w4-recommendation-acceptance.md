# W4 Recommendation Acceptance Evidence

Status: **PARTIAL — actual AWS happy path passed; negative matrix and teardown remain open**

This file intentionally does not claim full T104 completion. On 2026-09-20 an authorized operator
provisioned the isolated database, Main Queue/DLQ, W1 private adapter/relay and actual W4 worker,
then completed one fixed synthetic execution through AWS SQS. The run durably finished
`LIMITED` with `result_origin=ENGINE`; one publication, one candidate and one published outbox row
were recorded. The remaining negative/restart/DLQ matrix and teardown are still open.

## Local readiness proof (not T104 completion)

On 2026-09-20, an isolated local PostgreSQL database and private Docker network executed the
delivered W4 `W1ExecutionAdapter` through W1's authenticated private HTTP RunStore. The run
finished with `status=LIMITED`, `result_status=LIMITED`, `result_origin=ENGINE`, and
`execution_status=PUBLISHED`. Exactly one publication and one candidate were committed. Replaying
the same owner/run reference returned successfully and left both counts at one.

This check also proved the forward migration head `033_w4_candidate_result_version` and the
128-character candidate result-version boundary required by W4's `w4-` plus SHA-256 identifier.
The local run used the fixed no-network synthetic client and did not provision, send to, or claim
evidence for AWS SQS/IAM. Its temporary containers and network were removed after verification.

Local automated checks recorded before AWS provisioning:

- W1 Ruff check: passed.
- W1 W4 contract/service/runtime/preflight tests: 17 passed.
- W4 HTTP RunStore and synthetic-acceptance bootstrap tests: 4 passed.
- W1 contract manifest tests: included in the 17-test result and pin the criteria catalog and
  C01 knowledge schema used by the delivered runtime.

## Required immutable pins

Record these immediately before execution:

- W1 full commit SHA: `e843e7c5f21869fdb3a7dfde1778fdca7fde56b2`
- W1 image manifest digest:
  `sha256:a05ecf58570e768d9f9a0778a1288e1c5c6005aa5aa62ef8a820a9aee276d42e`
- W4 full commit SHA: `df41433218918e4167784243dc9b88e5a858278d`
- W4/W1 integration source SHA: `e36f2ac6ff9e83c3a9c9c0c2672455f9cfbe7ad6`
- W4 image manifest digest:
  `sha256:f36f65a9b57d8544e8eb873d5396b06f92dc2ba568e3ede49ca847a200f2593b`
- provenance note: the W4 upstream SHA did not contain `epick_w4.w1_runtime`; the executable
  worker image therefore combines that pinned upstream with the W1-owned integration module at
  the integration SHA above. Both revisions are required to reproduce this image.
- adopted schema manifest SHA-256:
  `sha256:bf69d0f6b10bbeeb2cded79788758fa800b148782e19347edcae44ad12a460d3`
- Main Queue ARN:
  `arn:aws:sqs:ap-northeast-2:940348258481:epick-staging-t104-w4-recommendation`
- DLQ ARN:
  `arn:aws:sqs:ap-northeast-2:940348258481:epick-staging-t104-w4-recommendation-dlq`
- W1 sender role ID: `AROA5V4I7XCY7Q26NCLVY`
- W4 worker role ID: `AROA5V4I7XCYZR73UHITS` (distinct from W1)
- isolated database identifier: `epick_w1_w4_t104_runtime`; no credentials are recorded here

## Acceptance matrix

Each row requires command/output evidence, database counts, Main/DLQ depth, and a cleanup result.

| Scenario | Required result | Status |
| --- | --- | --- |
| actual W4 synthetic success | one ENGINE/LIMITED publication and selectable candidate | **passed** — worker received/acknowledged 1 with zero retry/terminal rejection; run/result `LIMITED`, origin `ENGINE`, binding `PUBLISHED`, publication/candidate/outbox counts `1/1/1` |
| duplicate delivery | one publication/candidate set, duplicate acknowledged | pending |
| response loss after commit | replay acknowledged from identical lease and digest | pending |
| relay/worker restart | durable dispatch recovered, no duplicate publication | pending |
| lease expiry | old lease rejected, bounded retry or new fenced lease | pending |
| cancellation before publish | publication rejected, no candidates | pending |
| owner deletion epoch change | publication rejected, no private re-exposure | pending |
| question/snapshot change | stale publication rejected | pending |
| Source-currentness change | stale dependency publication rejected | pending |
| bounded retry and DLQ | max receive 5 redrives one retryable poison message | pending |
| public browser read/select | unchanged W1 API reports ENGINE and LIMITED | pending |
| queue drain and teardown | Main/DLQ zero; isolated secrets/queues/DB removed | pending |

## Actual happy-path execution record

- run label: `w1-w4-t104-20260920-01`
- mutation-free preflight: `status=ok`, immutable images configured, source/schema pinned,
  identities distinct, encrypted redrive-bound queue, authenticated private HTTP/database,
  REAL disabled and synthetic acceptance explicitly enabled
- W4 worker transport result:
  `received=1, acknowledged=1, retry_scheduled=0, terminal_rejected=0`
- W1 database result: run `status=LIMITED`, `result_status=LIMITED`,
  `result_origin=ENGINE`, completed=true
- binding result: `execution_status=PUBLISHED`, `attempt_no=1`, published=true
- durable row counts: publications=1, candidates=1, published outbox=1
- discovered deployment defect: the relay initially finalized its outbox as
  `OUTBOX_W4_RECOMMENDATION_REFERENCE_NOT_FOUND` because the worker could not see
  `recommendation_runs` through RLS. The isolated database was repaired with an explicit
  `epick_worker` policy and the exact failed row was reset once; the retry then passed.
- repository remediation: the runtime privilege manifest, preflight and integration assertion now
  require `recommendation_runs_worker_execution_policy`. Static lint and 10 focused runtime tests
  passed locally. The PostgreSQL integration test could not run locally because the existing
  localhost database password did not match the repository test credential; CI remains required.
- frontend submission check: the ENGINE result component and public API configuration tests passed
  (`2` files, `7` tests). A production Vinext build with
  `NEXT_PUBLIC_W1_API_URL=http://localhost:8000` and
  `NEXT_PUBLIC_EPICK_RESULT_MODE=live` completed all five build stages successfully. The localhost
  URL is a build-time contract check, not a staging deployment claim.

## Mandatory safety assertions

- `W4_RECOMMENDATION_REAL_DATA_ENABLED=false` throughout the run.
- No raw question, episode, Source URL, bearer token, or database URL appears in SQS or evidence.
- The W4 role has no RDS, purge, or W1 send authority.
- Question Core CT-12 resources and evidence are not reused as recommendation-execution proof.
- Synthetic acceptance does not approve REAL execution.
