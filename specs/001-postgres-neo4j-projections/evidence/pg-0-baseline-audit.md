# PG-0 baseline audit

> Recorded: 2026-09-14  
> Status: local repository and PostgreSQL evidence complete; RDS evidence pending.

## Observed local baseline

- Service branch: feat/BE at 72ff6c7, with pre-existing uncommitted backend and specs changes.
- Alembic chain: 000_database_test_baseline → 001_create_identity_tables →
  002_create_experience_repository.
- The current 001 users table has no deletion_epoch. A forward-only revision
  003_identity_contract_completion is required; no applied revision is edited.
- auth_identities persists provider/provider_subject with a unique pair. For PG-0 this is the
  physical mapping of the W1 logical issuer/subject pair; its uniqueness meaning is unchanged.
- Existing 001/002 use FORCE RLS and transaction-local app.current_user_id policy expressions.
- The previous runtime-role SQL was test-only. The deployment template is now
  backend/infra/postgres/runtime_roles.sql.

## Contract input audit

- W1/common v1 contracts are authored by the Service from W1_response.md.
- Observed W2 runtime source is C:\dev\EPICK_Engine\crawler at
  0865ecdfe4748dad5679bc82b9f7386dc663675e,
  src/epick_engine/source_collection/contracts.py.
- That source declares w2.collection.v1 and w2.source.v1. Its expected static Schema/examples
  files are absent from the W2 checkout, so backend generated and pinned static artifacts directly
  from the observed runtime models and test payloads at that immutable commit. The private W1↔W2
  contract is adopted; W1/W3 public Source Event consumer compatibility remains separately pending.

## Executed local database evidence

- Docker Compose PostgreSQL 16 was healthy. A native Windows `postgres.exe` owns host port 5432,
  so host pytest would target a different PostgreSQL instance. The suite was consequently run in
  an ephemeral Python container on the same Docker network, using only `epick_test`.
- `tests/integration/db/test_migrations.py`,
  `tests/integration/db/test_identity_and_idempotency.py`, and
  `tests/integration/db/test_rls_context.py` passed: **11 passed** (2026-09-14).
- The run covers blank/forward migration through revision 003, the deletion-epoch database
  constraint, owner RLS context isolation, and runtime-role DDL/RLS-bypass restrictions.

## Unavailable evidence

- No RDS endpoint, migration-principal secret, current alembic_version, role catalog, or
  backup/restore evidence is available in this workspace. Production readiness is not claimed.
