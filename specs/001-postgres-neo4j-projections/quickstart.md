# PostgreSQL Plan Validation Quickstart

This guide validates the database implementation plan locally. It does not authorize an RDS rollout,
live SQS dispatch, external egress, or Neo4j writes.

## 1. Prepare local PostgreSQL

From repository root, start the project Compose PostgreSQL service. The service is PostgreSQL 16
and uses the local development credentials defined in compose.yml.

    docker compose up -d postgres

Wait until the container health check reports healthy. The backend test fixture creates the isolated
test database when the configured PostgreSQL account has database-creation permission.

## 2. Run the current baseline evidence

From the backend directory, use the project's Python environment to run:

    pytest -m postgres
    pytest tests/contract

The migration suite must exercise blank-database upgrade, downgrade, and re-upgrade. The RLS suite
must use the runtime role and prove that owner context is transaction-local.

## 3. Before creating the next migration

1. Inspect the actual Alembic head and schema; never assume a revision is unapplied.
2. Compare the existing identity/Experience schema to DB v1.5, especially deletion epoch and
   issuer/subject semantics.
3. Validate W1 Job/Command/Checkpoint and common/W2 fixtures against their versioned schemas.
4. Add or update the failing real-PostgreSQL test first.
5. Create one forward migration that contains only the phase's parents, FKs, indexes, and checks.

## 4. Per-revision exit sequence

1. Upgrade an empty database to head.
2. Upgrade a database that contains the preceding revision.
3. Run owner/RLS and cross-scope FK tests.
4. Run phase-specific immutable Version, transaction, and concurrency tests.
5. Validate public/private Outbox payload boundaries when applicable.
6. Test downgrade only when the phase's approved data-loss behavior is explicit; production
   rollback otherwise uses a forward corrective migration.
7. Record revision ID, tests, and open Gates in the phase evidence file.

## 5. RDS release sequence

1. Confirm backup/restore and the exact current Alembic revision.
2. Run migration using the dedicated migration principal.
3. Verify API/worker runtime principals have no DDL or BYPASSRLS privilege.
4. Run post-migration smoke tests using a runtime role, not the migration account.
5. Only then proceed to the dependent API, private worker, or projection Gate.

Do not activate W3 typed consumption or Graph/Vector retrieval until D-05 is adopted and the
separate projection/deletion/rebuild evidence is complete.
