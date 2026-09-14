# Research: PostgreSQL Implementation Decisions

**Scope**: This records settled PostgreSQL implementation decisions from the current PRD, common
baseline, W1 platform contract, integration checklist, DB guide v1.5, W1 response, and W3 hold.

## R-01: PostgreSQL remains the sole authoritative record

**Decision**: Store identities, private data, immutable Versions, Snapshot membership, Job state,
user decisions, source lineage, Outbox/Inbox, and deletion state in PostgreSQL.

**Reason**: The PRD requires ownership, version reconstruction, Snapshot reproducibility, and
deletion boundaries that cannot be delegated to a derived graph or queue.

**Consequence**: Neo4j/Vector failure never rolls back the source transaction. It is rebuilt from
PostgreSQL only after its own later Gate.

## R-02: Existing Alembic revisions are immutable once shared

**Decision**: Continue from the current head. Reconcile a document-required field or constraint
with a new forward revision after checking actual Alembic history.

**Reason**: Editing a revision that may already be present in an RDS or teammate database makes
schema history ambiguous.

**Consequence**: The first implementation activity verifies current schema against the DB guide,
including users.deletion_epoch and the identity mapping semantics.

## R-03: Row isolation needs both relational and RLS enforcement

**Decision**: Private tables carry direct ownership where practical, use owner-bearing composite
foreign keys, and have ENABLE/FORCE RLS. Runtime requests set owner context transaction-locally.

**Reason**: Composite FKs reject invalid cross-owner links; RLS prevents a forgotten query predicate
from becoming a data disclosure. Transaction-local context avoids connection-pool leakage.

**Consequence**: A migration is incomplete without real PostgreSQL A/B-owner and pooled-connection
tests using a non-BYPASSRLS runtime role.

## R-04: Job acceptance is atomic but dispatch is separate

**Decision**: An accepted request atomically stores its idempotency outcome, Job, typed Job inputs,
Command, and Outbox record. Accepted means durable acceptance only, not worker claim or success.

**Reason**: The W1 contract requires retry-safe 202 responses while external dispatch can fail or
be delayed.

**Consequence**: Job status, completeness, required action, and dispatch status use separate
storage. Public event payloads cannot become a shortcut for private Job state.

## R-05: At-least-once delivery is handled with Outbox and Inbox

**Decision**: An Outbox relay claims committed rows with row locking and sends through SQS Standard.
Each consumer records a receipt keyed by consumer name and event ID.

**Reason**: Crash and redelivery are expected; exactly-once is not provided by the queue path.

**Consequence**: Redeliveries, reverse revisions, and gap recovery are test cases. A lower revision
may not revert a current projection state.

## R-06: Slots, fences, and deletion epochs are database invariants

**Decision**: Create three slot rows per owner. A claim transaction locks an empty row, verifies
the Job fence and owner epoch, creates the lease, and transitions the Job. Cancellation holds the
lease until worker acknowledgement.

**Reason**: Counting requests or relying only on worker memory cannot guarantee the per-user limit
or prevent a stale worker from committing later.

**Consequence**: Fence/epoch checks belong in the same result or checkpoint commit transaction.
Deletion advances the epoch and invalidates active fences atomically.

## R-07: W3 contract detail is deliberately excluded

**Decision**: Do not persist a typed W3 ACK/event/usability model until D-05 has a jointly adopted
schema and fixtures.

**Reason**: The current W3 restriction document is explicitly a draft, and its names, fields,
enums, replay meaning, and W4 use condition are not stable.

**Consequence**: Projection sync state is generic PostgreSQL readiness infrastructure; it is not a
W3 ACK ledger. A future adoption adds only an additive migration and adapter.

## Open operational evidence, not new schema decisions

- Confirm actual RDS version, extensions, role privileges, backup/restore, and existing migration
  history before production rollout.
- Produce W1/W2/common fixture validation before the Job/Outbox integration path.
- Prove Linux worker slot/cancel/late-result behavior and egress guard behavior in their dedicated
  isolation environments before enabling them.
- Keep retention periods, embedding dimension, queue fairness, and AWS capacity configuration out
  of migration constants until their respective evidence is approved.
