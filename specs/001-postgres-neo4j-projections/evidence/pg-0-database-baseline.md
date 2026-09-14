# PG-0 database baseline evidence

> Recorded: 2026-09-14  
> Status: local implementation and runtime validation passed; RDS evidence pending.

## Prepared changes

- Forward revision 003_identity_contract_completion adds users.deletion_epoch with default zero
  and a non-negative database check.
- Migration environment prefers DATABASE_MIGRATION_URL for an explicit Alembic run and otherwise
  retains DATABASE_URL. API/worker startup does not invoke Alembic.
- Runtime role template separates migrator from runtime/worker/deleter group roles and removes
  schema CREATE from runtime paths.
- Migration and identity tests now cover upgrade from 002 to head and deletion-epoch constraints.

## Executed static validation

- `python -m alembic upgrade head --sql` generated PostgreSQL DDL successfully through
  `003_identity_contract_completion`.
- `python -m ruff check app migrations tests` and Python compilation passed.
- The full contract suite passed: 24 tests, including the adopted W2 Command/Result/Source Event
  schemas, fixtures, and manifest checksum checks.

## Executed local runtime validation

- Docker Compose PostgreSQL 16 is healthy. A native Windows PostgreSQL service owns host port
  5432, so the suite ran in an ephemeral container on the Compose network rather than against the
  ambiguous host listener.
- The isolated `epick_test` database passed migration, identity/deletion-epoch, and RLS/role
  integration tests: **11 passed** (2026-09-14).
- jsonschema 4.26.0 was installed after adding the pinned development dependency, and contract
  fixture validation now passes.

## Remaining external validation

- RDS migration, RDS role privileges, RDS backup/restore, and a pre-existing RDS upgrade path have
  not been verified.

The local PG-0 implementation is validated. Private C-01 is adopted from the pinned W2 runtime
implementation; public Source Event consumer compatibility and the RDS evidence above remain.
