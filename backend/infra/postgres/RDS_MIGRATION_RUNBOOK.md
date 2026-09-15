# RDS PostgreSQL migration runbook

Use this runbook for an approved release only. API, worker, and deletion
processes never run Alembic during startup.

1. Record a verified RDS automated/manual backup and restore evidence timestamp.
2. Set `MIGRATION_DATABASE_URL` to a dedicated migration login. Set
   `DATABASE_URL` separately for the runtime login; do not reuse the migration
   URL for API or workers.
3. As an RDS administrator, apply `runtime_roles.sql` and assign the production
   login principals to the NOLOGIN group roles. Verify only the migration
   principal can create schema objects.
4. Run the read-only preflight before migration:

   ```powershell
   $env:MIGRATION_DATABASE_URL = '<dedicated migration connection URL>'
   python scripts/postgres_preflight.py --backup-confirmed-at 2026-09-15T00:00:00Z
   ```

5. Apply the forward-only revision exactly once with the migration principal:

   ```powershell
   python -m alembic upgrade head
   ```

6. Run the preflight again with `--require-head`, then start runtime processes
   with only `DATABASE_URL` configured.

The preflight confirms revision, PostgreSQL version, and group-role privileges.
RDS backup/restore verification remains an operator-owned AWS control; its
timestamp is deliberately required as evidence rather than inferred from SQL.
