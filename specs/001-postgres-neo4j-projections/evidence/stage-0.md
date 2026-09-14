# Stage 0 exit evidence

**Completed**: 2026-09-12

## Implemented foundation

- Added explicit local and test PostgreSQL settings; the sample development database now uses the
  Compose database name `epick_local`.
- Added SQLAlchemy 2 Declarative metadata with deterministic naming conventions, synchronous
  session factory, and transaction-local owner context through `set_config(..., true)`.
- Added Alembic configuration with reversible baseline revision
  `000_database_test_baseline`.
- Added non-superuser, non-BYPASSRLS test role definitions for migration, runtime, worker, and
  deletion paths.
- Added synthetic v1 Service-to-Engine capability fixtures and a fail-closed compatibility
  evaluator. No Engine call or public HTTP route was introduced.

## Verification

- `python -m pytest -q` with an isolated temporary PostgreSQL 16 instance: **9 passed**.
  This includes Alembic upgrade → downgrade to base → re-upgrade and pooled RLS-context tests.
- `python -m pytest -q tests/test_health.py tests/contract`: **6 passed**.
- `python -m ruff check app tests` and `python -m ruff format --check app tests`: **passed**.
- The temporary test container was removed after verification. The existing user-owned
  `compose.yml` was not modified.

## Gate status

- G-02/G-04: open; no Vector metadata, DDL, index, or route activation exists.
- G-03/G-N3: Queue plus internal HTTP contract topology is fixed in ADR-001; Queue product and
  operational retry/DLQ settings remain open.
- G-N1: Engine readiness and owner/active/index-version filter smoke test are open; no Graph or
  Vector dispatch is active.
- G-10/G-N2: W3 is the logical schema/DTO owner; a named Engine maintainer is deferred until the
  Engine repository is created.

## Local environment note

The host's port 5432 resolved to a PostgreSQL instance that did not accept the Compose credentials.
For this run, the required real-PostgreSQL tests used a temporary isolated PostgreSQL 16 container
on a non-conflicting host port. This is a local-port collision only; it does not change the checked-in
Compose topology or application contract.
