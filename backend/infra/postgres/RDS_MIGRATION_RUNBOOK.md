# RDS PostgreSQL migration runbook

Use this runbook for an approved release only. API, worker, and deletion
processes never run Alembic during startup.

1. Record a verified RDS automated/manual backup and restore evidence timestamp.
2. Set `MIGRATION_DATABASE_URL` to a dedicated migration login. Set
   `DATABASE_URL` separately for the runtime login; do not reuse the migration
   URL for API or workers.
3. As an RDS administrator, apply the current `runtime_roles.sql` **before**
   Alembic. This release requires `epick_worker` and `epick_lookup` to exist
   because migration `022_w1_runtime_relay_access` adds scoped RLS policies for
   those groups. Assign production login principals to the NOLOGIN group roles.
   Verify only the migration principal can create schema objects.
4. Run the read-only preflight before migration:

   ```powershell
   $env:MIGRATION_DATABASE_URL = '<dedicated migration connection URL>'
   python scripts/postgres_preflight.py --backup-confirmed-at 2026-09-15T00:00:00Z
   ```

5. Apply the forward-only revision exactly once with the migration principal:

   ```powershell
   python -m alembic upgrade head
   ```

6. As the same migration principal, apply the explicit runtime DML manifest:

   ```powershell
   python scripts/apply_postgres_runtime_privileges.py
   python scripts/postgres_runtime_privilege_preflight.py
   ```

   The manifest is deny-by-default for future tables. Extend and re-apply it
   whenever a later migration introduces a table consumed by the API, worker,
   or deletion process. Re-apply it for a release that changes the manifest
   itself even when that release has no Alembic revision.

7. Run the migration preflight again with `--require-head`, then start runtime
   processes with only their dedicated DB URL configured. The API, worker,
   lookup adapter, and deletion logins must be separate LOGIN principals
   inheriting `epick_runtime`, `epick_worker`, `epick_lookup`, and
   `epick_deleter`, respectively.

The preflight confirms revision, PostgreSQL version, and group-role privileges.
RDS backup/restore verification remains an operator-owned AWS control; its
timestamp is deliberately required as evidence rather than inferred from SQL.
