# Implementation Plan: PostgreSQL Authoritative Foundation

**Branch**: feat/BE | **Date**: 2026-09-14 | **Spec workspace**: 001-postgres-neo4j-projections  
**Canonical plan**: [POSTGRESQL_IMPLEMENTATION_PLAN.md](../../backend/docs/agents/POSTGRESQL_IMPLEMENTATION_PLAN.md)

## Summary

Implement EPICK's PostgreSQL system of record in forward-only stages. PostgreSQL owns identity,
personal data, immutable Versions, Snapshots, Job/Command/Checkpoint state, slots, fences,
deletion epochs, source lineage, Outbox/Inbox, and projection state. Neo4j/Vector are rebuildable
consumers and are not part of this implementation step.

The plan continues the existing Alembic chain (000 → 001 → 002) rather than recreating it.
First audit the applied chain and close required identity/deletion-epoch gaps with a forward
revision. Then implement P0 workspace/Job acceptance, P0 Snapshot/selection, P1 knowledge and
projection-state, and P2 lifecycle/deletion in separately tested revisions.

## Technical Context

**Language/Version**: Python >=3.10; current backend targets Python 3.10.  
**Dependencies**: FastAPI, SQLAlchemy 2.x, Alembic, psycopg 3, pytest.  
**Storage**: PostgreSQL 16 local Compose and private RDS target; PostgreSQL is authoritative.  
**Testing**: pytest against real isolated PostgreSQL, Alembic upgrade/downgrade, RLS, ownership,
transaction, concurrency, and contract fixtures. SQLite-only testing is insufficient.  
**Deployment boundary**: only the migration principal has DDL; API/worker principals do not run
migrations on startup and do not have BYPASSRLS.  
**Out of scope**: API route implementation, live SQS relay/worker, W3/Neo4j, Vector, and AWS
topology detail.

## Constitution Check

| Principle | Plan compliance |
|---|---|
| FastAPI standard | This plan changes persistence boundaries only; later HTTP behavior stays router/schema/service/repository separated. |
| PostgreSQL is SoR | All primary facts, permissions, Version/Snapshot and Job state are in PostgreSQL through Alembic. |
| Tests required | Each revision has real PostgreSQL exit tests before a dependent revision starts. |
| Contracts preserved | W1/W2/common schemas are frozen as fixtures before Job/Outbox DDL. W3 draft detail remains a hold. |

## Phase Plan

### Phase PG-0 — Contract and database baseline

1. Inventory RDS/local alembic_version, schema, roles, and backup/restore ability.
2. Do not change an applied revision. Add a forward revision for any required gap, notably
   users.deletion_epoch; preserve the (issuer, subject) identity meaning.
3. Freeze W1 Job/Command/Checkpoint, common envelope, and W2 Source command/result/event v1
   schemas and fixtures.
4. Provision and verify migrator/runtime/worker/deleter roles; test RLS through the runtime role.
5. Run blank and pre-upgraded DB migration paths plus pooled transaction-local owner isolation tests.

**Exit**: versioned fixtures and current-head migration/RLS evidence exist. No live worker or
RDS schema rollout occurs merely because planning is complete.

### Phase PG-1 — P0 workspace, Job, and atomic acceptance

Create, in coherent forward revisions: minimal company, application Project/Version,
Question/Version, Job, typed Job inputs/actions/commands, Outbox, Inbox, owner execution slots,
and Job execution leases.

Enforce owner/project composite FKs; stable-ID/Version pointer integrity; the approved Job status
catalog; separate completeness/actions/dispatch status; atomic Job+Command+Outbox acceptance;
and three actual owner slots via locked slot rows. CANCEL_REQUESTED holds its lease until worker
ACK. A result commits only when fence and deletion epoch still match.

**Exit tests**: idempotency replay/conflict, crash recovery, four-concurrent-claim rejection,
cancel/worker-ACK behavior, fence/epoch late-result rejection, public/private Outbox boundary.

### Phase PG-2 — P0 Snapshot and selection

Create Snapshot, Snapshot Episode Version membership, Recommendation Run/Candidate, and material
selection tables with their deferred composite FKs. A Run may use only its Project's Question,
Snapshot, and owner-scoped Episode Versions. Existing Snapshot and selection history never move
when a current Version changes.

**Exit tests**: cross-project and cross-owner links rejected; Snapshot immutability; candidate
membership; one ordered current selection set per Question.

### Phase PG-3 — P1 public knowledge and evidence

Split into revisions for Source/company lineage, postings/requirements, claims/interpretations,
and question analysis/Snapshot evidence. Add every P1 FK only with its actual parent table.
Maintain physical separation between public interpretations and private Project interpretations.
Use private job_source_links for Source-to-Job correlation.

**Exit tests**: Source policy and Version history, evidence/company scope, public/private
separation, fixed Snapshot evidence, and no fabricated source success.

### Phase PG-4 — P1 projection readiness

Add projection_sync_states, experience exclusions, and Snapshot exclusions. Reuse the P0
outbox_messages transaction boundary; state is PENDING, STALE, ERROR, or SYNCED and cannot
regress from a delayed revision. PostgreSQL source writes remain successful during a projection
outage.

**Hold**: do not create W3 ACK ledger/enum/table, W3 public endpoint, or W4 usability state until
D-05 has an adopted schema and fixture. Do not enable GRAPH/VECTOR retrieval in this phase.

### Phase PG-5 — P2 lifecycle and deletion

Add inference and duplicate decisions, checkpoints/notifications, settings/consent/feedback,
sensitivity controls, then deletion request/target orchestration. Deletion atomically advances the
owner epoch, invalidates active Job fences, and emits private deletion work. Completion waits for
all typed private targets at the same epoch and restore revalidation.

## Data and Contract Artifacts

- [research.md](research.md): settled decisions and Gate behavior
- [data-model.md](data-model.md): phased PostgreSQL relationship map
- [contracts/postgresql-boundaries.md](contracts/postgresql-boundaries.md): DB-facing message and
  privacy boundaries
- [quickstart.md](quickstart.md): local/CI validation sequence

## Project Structure

    backend/
    ├── app/{api,core,db,models,repo,schemas,services}/
    ├── migrations/versions/             # forward Alembic revisions only
    ├── tests/contract/                  # versioned W1/W2/common fixture validation
    └── tests/integration/db/            # real PostgreSQL migrations, RLS, constraints, concurrency
    backend/contracts/{common,w1,w2}/v1/ # required before Job/Outbox implementation

## Completion Criteria

A phase is complete only when its migration chain, downgrade strategy, real PostgreSQL tests,
contract fixtures, and relevant integration-checklist rows have evidence. Passing ORM unit tests,
a successful API startup, or a Neo4j-free happy path alone is not completion.
