# Stage 1 exit evidence — P0-A identity and idempotency

**Completed**: 2026-09-12

## Migration and Service implementation

- Added Alembic revision `001_create_identity_tables` after the Stage 0 baseline.
- Created `users`, `auth_identities`, `auth_sessions`, and `idempotency_records` with named
  primary keys, foreign keys, unique constraints, and checks.
- Enforced `users.account_status` values from the DB v1.3 catalog.
- Enforced OAuth `(provider, provider_subject)` uniqueness; an email alone is never used to merge
  identities. Unverified provider email is not adopted as the account email.
- Persisted only `refresh_token_hash`; no raw refresh-token column, value, response body, or OAuth
  access-token field was introduced.
- Enabled and forced PostgreSQL RLS with owner-aware policies on all four private tables.
- Added owner-scoped repository operations and safe idempotency replay/conflict service behavior.
  `response_ref` stores a re-queryable safe reference rather than an entire response payload.

## Verification

- Real PostgreSQL 16 integration suite: **14 passed**.
- Verified blank-database upgrade → downgrade to base → re-upgrade.
- Verified duplicate OAuth subject rejection, safe Refresh-token schema, unverified-email
  non-linking, same-hash idempotent replay, different-hash conflict, and forced RLS catalog state.
- Under the non-superuser runtime role, User A cannot list, update, delete, or insert User B's
  private User row; the owner context is transaction-local.
- Ruff lint and formatting checks passed for `app`, `tests`, and `migrations`.

## Contract and gate status

- No public authentication endpoint, OAuth provider, token issuer, token hash algorithm, or new
  external authentication dependency was chosen; G-01 remains open.
- Stage 0 Engine/Vector gate status is unchanged. No Graph, Queue, or Vector implementation was
  activated by this Stage.

## Next stage

Stage 2 adds only the immutable Experience repository and its owner/Activity composite integrity.
