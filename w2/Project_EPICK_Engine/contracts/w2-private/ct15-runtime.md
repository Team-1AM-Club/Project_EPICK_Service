# W2 CT15 runtime — local implementation / deployment pending

2026-09-27 private-authority runtime correction: current CT15 preflight requires
exactly one Alembic head, `0013_deletion_ack_confirmed`. Revisions `0005`
through `0011` are forward-migration starting points only, not CT15
runtime-ready heads. The `0011` migration retains only the original ACK control
graph needed for exact historical replay after private payload deletion, and
`0012` binds each retained ACK to the digest of its original canonical JSON wire.
Preflight rejects older, unknown, multiple, and duplicate heads before it reads
any queue metadata. The metadata-only preflight does not grant runtime authority.

2026-09-20 follow-up: [B1 scoped inspection and transport controls](ct15-b1-controls.md)
supersedes the historical missing-harness explanation below. The new W1 action
harness exists at `dc3a7b45e1297d5a5a66ee6a00cbec55f073f97d`, but its exported/stored
seed command violates the unchanged W2 policy/resume contract and the reported
tracked runbook is absent. T089 requires a corrected W1 pin; actual queue/image
execution remains pending. Existing migration head compatibility includes the
independently implemented `0006_source_restriction` and `0007_restriction_receipt`.

Updated: 2026-09-19. Deployment and infrastructure belong to W1. The joint
environment is not selected. No image was published and no AWS message was sent
as part of this implementation. This is not a joint CT15 completion receipt.

## Private-deletion scope v2 handoff (2026-09-24)

W2 publishes the current scope-owned payload contracts under
`contracts/w2-private/`:

- `private-deletion-command-v2.schema.json` defines
  `w2.private-deletion.v2` with an explicit ACCOUNT or PROJECT scope.
- `private-deletion-ack-v2.schema.json` defines
  `w2.private-deletion-ack.v2` and echoes the exact owner, epoch, and scope.

The W2 consumer seam is
`epick_engine.source_collection.private_deletion_v2.process_private_deletion_v2`.
W2 commits its owner-locked scope deletion and durable receipt before calling
W1's scope purge and ACK side effects. A stale command invokes neither side
effect. An exact current-epoch replay returns `DUPLICATE` and retries the W1
scope purge and ACK without repeating destructive database work.

W1 must add the W2 v2 deletion target and `0010_private_deletion_scope_v2`
migration, provide authenticated explicit scope proof for every collection and
commit-gate private write, bind outer-envelope owner/target/epoch/scope to the
inner v2 command, dispatch and apply the v2 ACK, purge W1 private references by
scope, and serialize each owner's deletion epochs until purge and ACK complete.
The production W1 authority adapter, outer envelope, dispatcher, ACK application,
AWS/SQS/IAM deployment, and joint account/Project run remain outside this local
W2 evidence. No current READY claim follows from the local contracts or tests.

## Historical T067 private-deletion v1 handoff (2026-09-23)

This section records the implemented v1 baseline only. W1 must not dispatch v1
on the current production deletion seam or interpret a v1 receipt as v2 scope
completion.

W2 publishes two private payload contracts under `contracts/w2-private/`:

- `private-deletion-command.schema.json` defines
  `w2.private-deletion.v1`.
- `private-deletion-ack.schema.json` defines
  `w2.private-deletion-ack.v1`, whose outcomes are `APPLIED`, `DUPLICATE`, and
  `STALE`.

The W2 consumer seam is
`epick_engine.source_collection.worker.process_private_deletion`. W1 passes a
validated `PrivateDeletionCommand`, a W2 database `session_factory`, and its
private side-effect adapter. The consumer finishes and commits the W2 database
transaction first, then calls `purge_private_references`, and only after that
purge succeeds calls `acknowledge`. A `STALE` result performs neither purge nor
ACK. A purge failure therefore leaves the transport unacknowledged; W1 retries
that transport/ACK failure with the same `deletion_id` so W2 can reuse the
durable deletion receipt without recreating private data. At this historical v1
snapshot, `migrations/versions/0009_private_deletion_receipt.py` was the exact
head required before either runtime preflight could pass.
At that time, a W1 operator applies Alembic through
`0009_private_deletion_receipt` before starting the runtime.

W1 owns authenticated dispatch, its private outer envelope and channel, binding
the authenticated owner to the W2 payload, AWS/SQS/IAM configuration, retry
scheduling, and ACK publication/transport. Those items, the W1 dispatcher, and
a joint T067 end-to-end run are outside W2 completion claims.
At that historical snapshot, W2 local T067 payload, consumer, and migration work
is complete; W1 authenticated dispatcher, AWS/SQS/IAM deployment, and joint T067
end-to-end validation remain incomplete.

The earlier W1 CT15 report was pinned to W2 database head
`0008_collection_runtime`, W2 source SHA
`11c005db5f94fe26c60314aec9b61764c166e379`, and a W1-reported image digest.
W2 confirmed only that the source object exists locally; it did not independently
verify that deployed image, ECR/SQS, the deployed database, or same-run restart
and count evidence. At that snapshot, runtime readiness required `0009`, so that
historical 0008 CT15 observation must not be presented as current READY evidence.

## Historical local verification snapshots (2026-09-19 through 2026-09-20)

Everything in this section is a dated historical snapshot, not current operator
guidance. Current preflight and deployment instructions require the exact
`0013_deletion_ack_confirmed` head described above and under Operator
commands.

T095 local-service update (2026-09-20): full regression is now **1286 passed,
15 failed, 1 skipped, 4 warnings** after aligning three obsolete restriction
test cases. Remaining failures are deletion (6), rendering (8), and actual
public restriction outbox emission (1). Internal authority/dedup service tests
passed; no real authority adapter or collection/queue caller was connected.
At that snapshot, the supported heads were 0005, 0006 and
`0007_restriction_receipt`.
Task/whole-change final review status is recorded in the feature validation log.

At the 2026-09-20 T095 Task 1 snapshot, the new additive head was
`0007_restriction_receipt` (file `0007_restriction_mutation_receipt.py`). CT15
preflight then accepted the known compatible 0005, 0006 and that exact 0007
head, retained required delivery-column checks, and rejected unknown future
heads.
Task 1 verification: 130 focused tests passed; full suite **1238 passed,
18 failed, 1 skipped**. The service stage remains in progress. No deployment
migration or actual AWS execution was performed. The dated results below are
historical snapshots, not the current head or a full integration receipt.

At the earlier 2026-09-20 snapshot, the additive restriction-storage migration
was `0006_source_restriction`. Preflight then accepted both
`0005_private_gate_delivery` and `0006_source_restriction`, retained required
delivery-column checks, and rejected unknown future heads. The isolated local
suite now reports **1229 passed, 18 failed, 1 skipped**; the obsolete W2 retry
route test was corrected to the W1-owned boundary. Remaining failures are
deletion (6), rendering (8), and restriction service/worker integration (4).
Restriction storage does not implement public delivery or consumer recovery.
No deployment migration, AWS run, commit or push was performed.

- Full suite on the approved isolated local PostgreSQL: **1218 passed, 19 failed,
  1 skipped, 4 warnings**. Failures remain in posting retry (1), account/Project
  deletion (6), rendered collection (8), and SourceRestriction (4). This is not
  an all-green release report; the new CT15 tests passed.
- Final Ruff lint and format checks passed (74 files); mypy passed (15 source
  files). CLI help and non-interpolating Compose validation exited 0.
- Actual PostgreSQL checks cover the additive 0004→0005 migration, scoped
  inspection, input larger than 16 KiB, and transaction rollback when the final
  staged wire exceeds 256 KiB. Command parsing retains its 16 KiB limit.
- SQS behavior was checked with test doubles, not an AWS deployment. Build,
  publish, workload authentication and the joint scenario harness remain pending.
- These changes are not yet committed/pushed as a new delivery revision.

## Contract baseline

W1 receipt: `W1_W2_COMMIT_GATE_ADOPTION_RESPONSE_2026-09-18.md` (written 2026-09-19).
Pinned Service revision: `bb27a692cf002100a3f773df7e434e90fe03f83f`.
Previously delivered Engine revision: `16a7bd2653873a20a563e6d2f54c24c6dc18c373`.
The original [81-file manifest](artifacts.sha256) belongs to that immutable Engine
revision, not this modified working tree. Schema/fixture snapshots and proposal
wire version names remain byte-compatible; their historical proposal labels are
not a statement that the latest W1 receipt withheld adoption.

W2 sends the adopted staged-result and ACK bodies directly to W1's dedicated
commit-gate inbound queue. It never sends them to the legacy collection-result
queue. Lease determination remains W1-owned. Exact gate replay reuses the
persisted original APPLIED ACK (message ID, timestamp, binding and outcome), and
re-arms its delivery marker. It never substitutes a new DUPLICATE success ACK.

## Execution scope and failure behavior

- This operator is **gate-only, synthetic CT15**, not a collection dispatcher.
  W1's normal command queue multiplexes collection, direct registration and gate
  traffic. Do not attach this operator as a competing consumer on that queue.
  W1 must supply an isolated test queue with only commit-gate traffic, and the
  operator must explicitly attest `W2_CT15_GATE_ONLY_QUEUE_APPROVED=true`.
  The flag is an operator assertion, not proof of AWS routing/policy correctness.
- `consume-once` authenticates the SQS system SenderId against the configured
  stable W1 role ID before parsing. JSON `producer` and message attributes cannot
  authenticate a sender. Receipt deletion follows durable state/ACK commit.
- Malformed, unauthenticated or state-invalid commands are not applied or
  deleted. They remain subject to the dedicated queue's retry/DLQ policy. There
  is no synthesized REJECTED ACK. W1's exact joint expectations for state-invalid
  commands must be established in the scenario harness; rollback/no-ACK must not
  be counted as successful application.
- `relay-once` selects one unsent staged result, otherwise one unsent ACK. It
  locks the command using the same PostgreSQL advisory transaction lock as
  terminal PURGE, re-reads the payload, sends it and records `delivered_at`.
  An uncertain send/commit retries the same persisted wire identity. If PURGE
  wins first, the erased staged body is not sent. If send wins first, subsequent
  W1 stale/epoch validation is still required: a sent SQS body cannot be recalled.
- Every CT15 consume or relay action uses the configured protected W1 authority
  client. Gate application, relay claim/release, and send obtain their own fresh
  operation-specific decision; a preflight or count-only inspection neither
  constructs an authority result nor authorizes a later effect.
- Local transaction atomicity does not extend across SQS or W1's database.
  Standard SQS delivery/order guarantees are not strengthened by this runtime.
- Store errors and SDK diagnostics are reduced to fixed statuses; body, DSN,
  token, receipt handle and private SQL parameters are not printed.

## Build and publish responsibility

Install local tools with `uv sync --locked --extra dev --extra ct15`.
`requirements.ct15.lock` is exported from `uv.lock`, with hashes and without dev
extras. The Docker context allowlist contains only code, migration and lock data.

After these changes are committed, use:

```text
python scripts/build_ct15_image.py --source-sha <full-commit> --base-image <approved-python-3.12-slim-image@sha256:digest> --tag <ct15-local-build-tag>
```

The helper validates immutable inputs and builds from `git archive <full-commit>`.
It never builds from ignored/uncommitted files. It verifies the image revision
label and reports `BUILT_NOT_PUBLISHED`, `source_sha`, `base_image` and
`local_image_id`. A local image config ID is **not** a registry manifest digest.
W1 publishes to its approved registry and records the actual immutable manifest
digest and source SHA before deployment. Manual Docker builds or OCI labels alone
are not provenance evidence. No registry/account/base-image digest is invented.

`compose.ct15.yaml` requires `W2_CT15_IMAGE_REPOSITORY` and
`W2_CT15_IMAGE_SHA256` (the 64 hex digits of the published manifest digest), so its
image reference is digest-pinned. It runs non-root with a read-only filesystem,
no capabilities and no-new-privileges. W1 supplies workload role credential
delivery and private network access; this file does not mount host credentials.

## Runtime settings (values through W1's approved channel)

| Variable | Meaning |
| --- | --- |
| `W2_CT15_ENABLED` | Exact `true`; explicit isolated-run opt-in |
| `W2_CT15_GATE_ONLY_QUEUE_APPROVED` | Exact `true`; W1 confirmed no competing collection/registration traffic |
| `W2_CT15_DATABASE_URL` | W2-owned PostgreSQL+psycopg DSN; database name contains a separate `ct15` token |
| `W2_CT15_REGION` | W1-selected AWS region |
| `W2_CT15_COMMAND_QUEUE_URL` | Dedicated Standard test queue; canonical HTTPS AWS SQS URL, `ct15` name token |
| `W2_CT15_INBOUND_QUEUE_URL` | W1's dedicated Standard staged-result/ACK inbound queue, distinct from command queue |
| `W2_CT15_EXPECTED_W1_SENDER_ID` | Real stable IAM role ID, no STS session suffix or ARN |
| `W2_CT15_RUNTIME_LABEL` | Operator-chosen isolated runtime label containing a `ct15` token |
| `W2_CT15_SYNTHETIC_INPUTS` | Exact `true` only when using the explicit `stage` fixture operation |
| `W1_LOOKUP_ENDPOINT` | Existing validated protected W1 HTTPS origin; no alternate authority URL |
| `W1_LOOKUP_BEARER` | Existing W2 service bearer supplied through the approved secret channel |
| `W1_LOOKUP_CA_FILE` | Existing CA bundle for the protected W1 origin |

W1 additionally supplies/records the actual W2 IAM role ARN and stable role ID
for inbound queue policy and `W2_COMMIT_GATE_EXPECTED_SENDER_ID`. W1 also provides
the W1 command-sender role ID used in the reverse direction. These identities
must be verified on actual workloads; they are not derived from JSON bodies.

No static AWS credential is part of this contract. Never paste raw queue URLs,
DSNs, credentials, tokens or private fixture bodies into Git or chat. Avoid
rendering Compose with secret interpolation into saved logs.

## Operator commands

Run from the Engine checkout with `uv run --no-sync epick-w2-ct15 <action>`, or
the image entrypoint with the same action.
A W1 operator applies Alembic through `0013_deletion_ack_confirmed` to the
approved W2 DB before starting the runtime. Revisions `0004` through `0011` are
forward-migration starting points, not runtime-ready heads. Preflight never
migrates the database.

The `0011` to `0012` rollout requires a coordinated stop: stop every W2 writer
and relay, apply and verify the digest backfill at the exact new head, then
restart the new binaries. Mixed-version or online writes during this migration
are unsupported.

| Action | Behavior |
| --- | --- |
| `preflight` | Read database name/migration/columns and queue ARN/encryption/DLQ metadata; no queue consumption or mutation |
| `consume-once` / `relay-once` | One bounded processing iteration; fixed status JSON |
| `consume` / `relay` | Continuous counterpart; finishes in-flight work on SIGTERM/SIGINT, exits nonzero on transport uncertainty |
| `stage --input <private-synthetic-file>` | Explicit test-only `{command,result}` input, strict JSON and bound size; validates and durably stages without a fabricated lease |
| `inspect --owner-ref <synthetic-uuid> --command-id <synthetic-uuid>` | Scoped count-only inspection using the command lock; no IDs, hashes, payloads or database access are handed to W1 |

Run consumer and relay as separate supervised processes. Queue calls have
bounded SDK timeouts and one SDK attempt; receipt visibility is 60 seconds.
The DB connection has a 5-second lock timeout and 15-second statement timeout.
W1 selects process supervision/restart policy. This is not an automatic source
fetch retry and does not alter the PRD's 429/user-retry rule.

The inspection output matches [ct15-inspection.schema.json](ct15-inspection.schema.json).
STAGED/PREPARED results have `visible_result_count=0`; FINALIZED has 1; terminal
PURGE/ABORT have 0 result and staged-payload counts. A wrong owner scope yields
zero counts. The inspection schema is a W2 test-interface proposal for W1 review.

`PREFLIGHT_PASSED` proves metadata checks only, not SendMessage/receive/delete
authorization, workload identity, deployed image provenance, physical purge of
backups, W1 consumer operation or joint CT15. Operators compare real account,
queue ARNs, attached roles and policies with W1's approved inventory separately.
No `PurgeQueue`, AWS provisioning, queue policy mutation or implicit teardown is
implemented here.

## Why the canonical joint harness remains pending

Pinned W1 CI expects `backend/tests/e2e/test_w2_commit_gate_e2e.py`; its existing
`test_w1_w2_commit_gate_ct15.py` is an always-skipped placeholder. W1's receipt
references `specs/005-w1-w2-commit-gate-adoption/quickstart.md`, which is absent
from the pinned repository (both root and `backend/` paths were checked).

More importantly, `backend/scripts/run_w1_w2_ct15_synthetic.py` is a receipt-only
inbound drain/probe. It has no operator surface to create current W1
User→Job→command→operation→lease bindings or trigger the full nine scenarios.
The lookup endpoint only reads currentness. W2 must not write W1's database to
invent this setup. W1 needs to provide its guarded seed/action harness half and
actual fixture/API credentials through the approved channel. W2 then combines
that half with these stage/relay/consume/inspection primitives and authors the
canonical joint test. A placeholder file must not open `W2_COMMIT_GATE_E2E_READY`.

Required W1 controls: normal current binding/dispatch, duplicate/redelivery,
same-ID conflict, ACK-loss/recovery timing, fence/epoch advancement, cancellation,
deletion/PURGE orchestration, late FINALIZE capture/replay, two-owner/shared-source
fixtures. Per-case assertions must combine W1's local operation/result/checkpoint
counts and W2's counts; neither side's local result substitutes for the other.

## Explicit remaining work

- Canonical CT15-01~09 harness and actual queue execution remain incomplete.
- Real collection/direct-registration multiplexing remains outside this gate-only
  test operator. Its consume and relay effects now use the same configured
  protected W1 authority client as the general source runtime.
- W2 local private-deletion scope-v2 payload, consumer, ACK-control retention,
  per-operation W1 authority adapter, write fences, and exact-head preflight work
  is complete at this source revision. The W1 outer envelope/dispatcher/ACK,
  scope purge, per-owner epoch serialization, AWS/SQS/IAM deployment, and joint
  account/Project validation remain incomplete.
- Revisions `0006_source_restriction`, `0007_restriction_receipt`, and
  `0008_collection_runtime`, `0009_private_deletion_receipt`, and
  `0010_private_deletion_scope_v2` are migration history below the required 0011
  head, not future migrations or runtime-ready alternatives.
- No current 0011 deployed-image digest or deployment evidence has been
  independently verified. The prior W1-reported 0008 image evidence does not
  establish current READY at 0011.
- W3 deployment, retention/monitoring policy and full-app/W4 integration remain
  separate responsibilities and are not completed by these local tests.

## Evidence references

- [W1 pinned outbox routing](https://github.com/Team-1AM-Club/Project_EPICK_Service/blob/bb27a692cf002100a3f773df7e434e90fe03f83f/backend/app/runtime/outbox_relay.py)
- [W1 pinned inbound worker](https://github.com/Team-1AM-Club/Project_EPICK_Service/blob/bb27a692cf002100a3f773df7e434e90fe03f83f/backend/app/runtime/w2_commit_gate_worker.py)
- [W1 pinned probe](https://github.com/Team-1AM-Club/Project_EPICK_Service/blob/bb27a692cf002100a3f773df7e434e90fe03f83f/backend/scripts/run_w1_w2_ct15_synthetic.py)
- [AWS ReceiveMessage system SenderId](https://docs.aws.amazon.com/boto3/latest/reference/services/sqs/client/receive_message.html)
- [AWS SendMessage checksum response](https://docs.aws.amazon.com/boto3/latest/reference/services/sqs/client/send_message.html)
