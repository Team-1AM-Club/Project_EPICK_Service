# W4 Recommendation Acceptance Evidence

Status: **NOT EXECUTED — AWS acceptance gate remains open**

This file intentionally does not claim T104 completion. T080–T103 provide the local code,
contract, database, browser, image, Compose, IAM-template, preflight, and runbook prerequisites.
The isolated AWS resources and actual W1/W4 synthetic execution must still be provisioned and
run by an authorized operator.

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

- W1 full commit SHA: pending
- W1 image manifest digest: pending
- W4 full commit SHA: `df41433218918e4167784243dc9b88e5a858278d`
- W4 image manifest digest: pending ECR publication
- adopted schema manifest SHA-256:
  `sha256:bf69d0f6b10bbeeb2cded79788758fa800b148782e19347edcae44ad12a460d3`
- Main Queue ARN and DLQ ARN: pending, redact account-specific evidence if shared externally
- W1 sender role ID and W4 worker role ID: pending, must be distinct
- isolated database identifier: pending; no credentials are recorded here

## Acceptance matrix

Each row requires command/output evidence, database counts, Main/DLQ depth, and a cleanup result.

| Scenario | Required result | Status |
| --- | --- | --- |
| actual W4 synthetic success | one ENGINE/LIMITED publication and selectable candidate | pending |
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

## Mandatory safety assertions

- `W4_RECOMMENDATION_REAL_DATA_ENABLED=false` throughout the run.
- No raw question, episode, Source URL, bearer token, or database URL appears in SQS or evidence.
- The W4 role has no RDS, purge, or W1 send authority.
- Question Core CT-12 resources and evidence are not reused as recommendation-execution proof.
- Synthetic acceptance does not approve REAL execution.
