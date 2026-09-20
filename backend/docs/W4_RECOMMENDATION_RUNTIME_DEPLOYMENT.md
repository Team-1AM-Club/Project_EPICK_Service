# W4 Recommendation Runtime Deployment

This runbook deploys only the synthetic acceptance boundary for W4 recommendation execution.
It does not approve or enable REAL user data.

## Release order

1. Pin the W1 full commit SHA, W4 full commit SHA, both immutable image digests, and the
   adopted schema-manifest SHA-256.
2. Pass T080–T103 in CI before creating or changing AWS resources.
3. Apply Alembic migrations through `033_w4_candidate_result_version` with the migration
   principal. Migration 033 is required because the delivered W4 result version is `w4-`
   plus a 64-character SHA-256 digest.
4. Apply `infra/postgres/runtime_privileges.sql` after the migrations, then run the runtime
   privilege preflight. A role created before the new tables exists does not acquire those
   table grants automatically.
5. Create a dedicated encrypted Main Queue and DLQ. Bind the DLQ with
   `maxReceiveCount=5`; do not reuse the Question Core queue.
6. Apply the queue and worker policies from the templates in `infra/` after replacing only
   the explicit ARN placeholders.
7. Store W1-private and W4-worker settings in separate Secrets Manager secrets. Materialize
   root-owned `0600` env files on their respective hosts; never place bearer tokens in Compose,
   logs, evidence, or the queue payload.
8. Start the private W1 RunStore service on a private network with no public host port. Allow
   ingress only from the W4 workload security group and require workload bearer authentication.
9. Start the W4 worker with its receive-only SQS role and the immutable W4 image.
10. Run the mutation-free preflight, then the isolated synthetic acceptance matrix. T104 is
    complete only after its evidence file records queue drain and cleanup.

## Required settings

W1 and W4 must receive only the settings required by their Compose service. Do not copy one
service's env file to the other. The shared bearer and audience have intentionally different
consumer-side names:

W1 private adapter and relay env file:

- `WORKER_DATABASE_URL`
- `AWS_DEFAULT_REGION`
- `W1_RECOMMENDATION_EXECUTION_MODE=ENGINE`
- `W4_RECOMMENDATION_EXECUTION_QUEUE_URL`, `W4_RECOMMENDATION_EXECUTION_DLQ_URL`
- `W4_RECOMMENDATION_PRIVATE_BASE_URL` (the private URL W4 will use)
- `W4_RECOMMENDATION_PRIVATE_BEARER`
- `W4_RECOMMENDATION_PRIVATE_AUDIENCE=epick-w4-recommendation-worker`
- `W4_RECOMMENDATION_ENGINE_SOURCE_REVISION`
- `W4_RECOMMENDATION_SCHEMA_MANIFEST_SHA256`
- `W4_RECOMMENDATION_REAL_DATA_ENABLED=false`

W4 worker env file:

- `AWS_DEFAULT_REGION`, `W4_RECOMMENDATION_EXECUTION_QUEUE_URL`
- `W1_RECOMMENDATION_PRIVATE_BASE_URL`
- `W1_RECOMMENDATION_PRIVATE_BEARER` (the same secret value as W1's
  `W4_RECOMMENDATION_PRIVATE_BEARER`)
- `W1_RECOMMENDATION_PRIVATE_AUDIENCE=epick-w4-recommendation-worker`
- `W1_RECOMMENDATION_HTTP_TIMEOUT_SECONDS=30`
- `W4_RECOMMENDATION_VISIBILITY_SECONDS=600`
- `W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE=YES`
- `W4_RECOMMENDATION_REAL_DATA_ENABLED=false`
- `W4_RECOMMENDATION_BOOTSTRAP=epick_w4.acceptance_bootstrap:build_acceptance_adapter`
- `W4_RECOMMENDATION_DISPATCH_SCHEMA_PATH=/contracts/dispatch.schema.json`

Compose invocation inputs (not env-file secrets):

- `BACKEND_IMAGE`, `W4_RECOMMENDATION_IMAGE`
- `W1_W4_RECOMMENDATION_ENV_FILE`, `W4_RECOMMENDATION_ENV_FILE`
- `W4_RECOMMENDATION_DISPATCH_SCHEMA_PATH`, `RDS_CA_BUNDLE_PATH`

Use `backend/infra/w4-recommendation-runtime.compose.yml`. Both image inputs must use
`repository@sha256:...`; tags alone are rejected by preflight.

## Identity and network boundaries

- W1 uses a send-only role for the recommendation Main Queue.
- W4 uses a distinct role ID with receive, delete, change-visibility, and attribute-read access
  to the Main Queue; it has no RDS, purge, or W1 send permissions.
- The DLQ is readable for attributes only by the runtime role. Operational redrive or purge is
  a separately approved operator action.
- The queue message contains only run/binding identifiers and pinned contract metadata. W4
  obtains content through authenticated private HTTP after acquiring a fenced lease.
- TLS is required in the queue policy and in transit to the private adapter.

## Preflight and acceptance

Run `python scripts/preflight_w4_recommendation_runtime.py`. A passing result must report:

- immutable W1/W4 image digests;
- matching W4 source and schema-manifest pins;
- distinct W1 and W4 workload role IDs;
- encrypted Main/DLQ and the expected redrive binding;
- safe HTTP timeout, heartbeat, and visibility bounds;
- authenticated W1 private health and worker database access;
- `real_data=disabled`.
- `synthetic_acceptance=explicitly_enabled`.

Prepare a single empty-database fixture with
`python scripts/prepare_w1_w4_t104_synthetic.py`. The command requires
`W1_W4_T104_PREPARE_SYNTHETIC=YES`, distinct seed/worker PostgreSQL principals, and a disposable
database whose name contains `w1`, `w4`, and `t104`. Its output contains identifiers only. Start
the W1 private adapter, recommendation-only outbox relay, and W4 worker through the Compose file;
the fixed T104 client makes no provider network calls and all published results remain
`ENGINE` plus `LIMITED` with an explicit no-network limitation.

The isolated acceptance matrix must cover success, duplicate delivery, response loss after
commit, worker restart, lease expiry, cancellation, owner deletion epoch, question/snapshot
change, Source-currentness change, bounded retry/DLQ, and empty queues after cleanup. Record
only redacted identifiers and counts in the evidence file.

## Monitoring

Alarm on Main Queue age/depth, DLQ depth, W4 terminal rejection count, lease expiry/retry rate,
private HTTP 5xx/timeout rate, and runs left in PENDING/RUNNING beyond the lease budget. Logs must
carry run ID and correlation ID but never raw context, bearer tokens, database URLs, or candidate
content.

## Rollback and teardown

1. Set the server-selected executor back to `SYNTHETIC`; existing ENGINE runs must not be
   relabelled or automatically regenerated as synthetic results.
2. Stop the W4 worker, wait for in-flight visibility leases, and retain failed messages for
   operator review. Do not purge as part of rollback.
3. Stop the private adapter after workers are quiescent. Database migrations 032 and 033 are
   forward-only; keep their rows for audit and deploy a corrective migration if necessary.
4. For an isolated acceptance environment, export redacted evidence, verify Main/DLQ are empty,
   remove test secrets and policies, delete the test queues, and delete the isolated database.

REAL execution remains blocked until a separate data-policy approval, privacy review, W4 model
approval, and explicit release gate are recorded. Synthetic T104 evidence cannot satisfy that
gate.
