# Research: W1–W3 Actual Runtime Integration

## Decision 1 — Feature boundary

**Decision**: Create feature 007 for actual runtime deployment and joint validation; preserve feature
003 as the completed W3→W1 inbound consumer implementation.

**Rationale**: Feature 003 proves the receiving transaction and T043 synthetic sender. M1–M5 adds a
separate deployed producer, Authority, deletion operations and workload IAM. Overwriting 003 would
blur completed evidence with external gates.

**Alternatives considered**: Extend 003 in place; rejected because it would turn an implemented
feature into a perpetually gated deployment project and invalidate its existing task/evidence history.

## Decision 2 — Reproducible W3 image

**Decision**: Treat `w3/Project_EPICK_Service` as a read-only independent clone, verify runtime
implementation SHA `66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b` is an ancestor of documentation
receipt HEAD `bad8671b2ea8d02bdce2157120b94d2b7edf09d8`, and prove no runtime-path drift. Export an
allowlisted build context from the implementation commit and build it with `uv.lock`, Python 3.12,
a non-root runtime user, read-only rootfs, bounded `/tmp` tmpfs and one `/state` volume. Push an
implementation-SHA tag but deploy only `repository@sha256:manifest`.

**Rationale**: The actual W3 Git objects are now locally verifiable, while the former `c7e678...`
handoff image is stale and its W1-owned Dockerfile was removed when the clone replaced the copied
tree. A Git archive prevents `.git`, `.venv` and working-tree-only files entering the image context;
the lockfile and digest deployment prevent a mutable branch or tag changing the tested runtime.

**Alternatives considered**: Install W3 directly on the host; rejected because dependencies,
identity and restart evidence would not be immutable. Build from an unpinned branch; rejected because
the reviewed W3 code could drift.

## Decision 3 — W3 state topology

**Decision**: All W3 CLI processes on one Worker EC2 share `/state/core.db` through one local Docker
named volume. Same-host processes are permitted because W3 serializes writes with `BEGIN IMMEDIATE`.
Multi-host, NFS and separate SQLite replicas are unsupported.

**Rationale**: This is the topology implemented and documented by W3. It preserves event revision,
delivery attempt and deletion tombstone ordering without inventing a distributed allocator.

**Alternatives considered**: RDS/PostgreSQL migration or NFS-shared SQLite; rejected because neither
is implemented by the handed-off W3 runtime and both would change its transaction contract.

## Decision 4 — Authority boundary

**Decision**: Define a versioned, private W1 Authority request/response containing only
`job_id`, `source_id`, current W3 DecisionContext, `owner_id`, `owner_epoch` and `active`. The W1
service derives owner/context from PostgreSQL; it never accepts a caller-supplied owner as authority.
Final route authentication and the W3 client adapter remain gated on W3-A/B.

**Rationale**: These fields exactly satisfy W3's existing `Authorization` model and avoid exposing
prompt/Source content or DB credentials. Keeping final auth gated honors the missing adapter owner
instead of silently making an inter-team contract decision.

**Alternatives considered**: Reuse the W2 command lookup payload; rejected because it is command/fence
oriented and exposes an unrelated contract. Let W3 query W1 PostgreSQL; rejected because it leaks DB
credentials and bypasses W1's authoritative service boundary. Use `SyntheticAuthority`; rejected for live use.

## Decision 5 — Authority semantics

**Decision**: W1 returns a current snapshot only after validating User→Job→Source ownership,
company/source/input binding, account deletion state and Job active/cancel state. Infrastructure
failures are retryable; authentication, absence, conflict and stale states are fail-closed. W3 must
recheck at supply, relay and replay.

**Rationale**: W3's runtime already calls `Authority.current` at each sensitive transition. W1 owns
the source-of-truth rows and can make the check without trusting the request body.

**Alternatives considered**: Cache a positive Authority result across relay/replay; rejected because
cancel/delete can occur after supply. Return `active=false` with hidden causes for every failure;
rejected because authentication/infrastructure retry behavior must remain distinguishable.

## Decision 6 — Deletion integration

**Decision**: Extend W1's existing PostgreSQL deletion target/outbox workflow with an explicit W3
Core runtime target. W3-C adopted separate private Standard-SQS command and receipt queues. W1 sends
with its workload role, W3 authenticates the SQS `SenderId`, and W1 accepts receipts only from the
configured W3 stable Role ID. W3 stores the canonical command digest internally and retransmits the
same receipt bytes; W1 binds the receipt by `command_id`, operation, target reference and current
epoch/effective time, and stores the receipt-body digest for idempotency. `APPLIED`/`DUPLICATE`
acknowledge the target while `STALE` records a retryable target failure. Same-ID/different-body
`CONFLICT` is a W3 terminal reject with no receipt. Body `producer` is never authentication evidence.

**Rationale**: W1 already fences jobs, increments deletion epoch and stages durable private-store
commands atomically. Adding W3 there gives crash-safe retry without coupling a PostgreSQL transaction
to Docker, network or SQLite.

**Alternatives considered**: Call the W3 CLI during account deletion; rejected due to cross-store
partial failure and lock duration. Delete W3 rows directly; rejected because W3 owns tombstones,
counters and secure-delete semantics. Introduce a W1 receipt API; rejected because the handoff
explicitly does not require one.

## Decision 7 — Runtime operations

**Decision**: W1-managed scheduling invokes bounded `relay-once`, `expire`, `inspect` and redacted
quarantine `backup` operations against the same state volume. `HELD` emits an alert and stops
automatic retry. Replay is an explicit, audited operator action after W3 technical review. Runtime
deadlines are the immutable `w3.retention/1.1` values; the CLI compatibility input is exactly
`1209600` seconds.

**Rationale**: W3's CLI is deliberately one-shot and its retry budget transitions to HELD. A bounded
scheduler makes each execution observable and restartable without wrapping it in an unreviewed
infinite loop.

**Alternatives considered**: Automatic HELD replay; rejected because the original cause may be
authorization, deletion or a persistent contract fault. Expire every state by wall clock; rejected
because PENDING/RETRY/HELD require resolution rather than deletion.

## Decision 8 — Backup and recovery

**Decision**: Permit only W3's redacted, quarantined backup for counter/tombstone inspection with the
policy-defined maximum lifecycle. Do not restore raw SQLite, Docker volume, filesystem or EBS
snapshots as live primary. W3-E is satisfied by `w3.retention/1.1`; no live reconstruction procedure
is approved.

**Rationale**: Raw restore can resurrect deleted event bodies and roll back revision/deletion state.
The handed-off backup intentionally disables supply/relay/replay.

**Alternatives considered**: Periodic volume snapshot restore; rejected as explicitly unsupported
and privacy-unsafe. Promote the quarantine backup to primary; rejected because no supported unlock exists.

## Decision 9 — IAM and SQS

**Decision**: Reuse the dedicated W3 Core Decision Main Queue/DLQ. Bind an actual W3 workload role
with only Main Queue `SendMessage`; retain W1 receive/delete/change-visibility and DLQ inspection
permissions. Verify STS stable Role ID equals both W3 expected role ID and W1 expected SenderId.

**Rationale**: SQS `SenderId` is the authenticated transport principal already enforced by the W1
consumer. Reusing the queue avoids a new wire or receipt service while replacing the synthetic T043 role.

**Alternatives considered**: Trust `producer=w3`; rejected because it is caller-controlled body data.
Share the W1 worker role; rejected because it would grant W3 receive/delete and broader W1 access.
Static access keys; rejected because workload credentials and rotation are required.

## Decision 10 — Joint completion evidence

**Decision**: M5 runs CT12-01~12 with actual W3 caller/supplier/outbox/relay, actual SQS and the W1
consumer/PostgreSQL. One evidence manifest binds source SHAs, image digests, role identity hashes,
scenario results, W3 inspect counts, SQS counts, W1 DB/action/command counts and cleanup.

**Rationale**: Transport acceptance is not W1 application acceptance. Cross-system reconciliation
and negative/race evidence are necessary to prove late writes and auto-execution remain blocked.

**Alternatives considered**: Mark complete after M1–M4 preflight; rejected because it proves readiness,
not service behavior. Reuse T043 as actual producer proof; rejected because T043 used a W1 harness sender.

## Resolved unknowns and explicit gates

No implementation choice is left as an unqualified `NEEDS CLARIFICATION`. W3-E is resolved by the
approved implementation. Missing inter-team values remain deliberate cutover gates: W3-A/B block
M2 actual binding, actual private Queue/Role injection and live evidence block M3 closure, M1/M2
block M4, and M1–M4 plus W3-F block M5. W3-C transport/ACK semantics and implementation SHA
`66a0e3e1...` are verified; W1-owned work may proceed without placeholder READY values.
