# Additive Public API Gaps

This document identifies public surfaces required by the delivered UI but absent from the current FastAPI routers. Exact Pydantic models become part of W1 OpenAPI and require contract tests before frontend consumption.

## 1. Activity deletion

### Preview

`POST /api/v1/activities/{activity_id}/deletion-previews`

Request:

```json
{"scope":"SOURCE_ONLY"}
```

Allowed scopes:

- `SOURCE_ONLY`: delete the Activity/Episodes and invalidate their future use.
- `INCLUDING_PERSONAL_SNAPSHOTS`: additionally delete owner-private snapshots/results where the Activity is material, subject to existing retention rules.

Response `201`: request ID, scope, affected resource counts/categories, expiry, one-time preview token.

### Confirm/start

`POST /api/v1/activities/{activity_id}/deletion-requests`

Requires `Idempotency-Key`, `If-Match`, preview request ID, and preview token. Returns `202` with generic deletion status URL.

## 2. Application-project deletion

### Preview

`POST /api/v1/application-projects/{project_id}/deletion-previews`

Fixed scope: `PROJECT_PRIVATE_SCOPE`. Preview states that source Activities/Episodes and public company knowledge remain.

### Confirm/start

`POST /api/v1/application-projects/{project_id}/deletion-requests`

Requires `Idempotency-Key`, `If-Match`, preview request ID, and preview token. Returns `202` and invalidates active project Jobs before any result can commit.

## 3. Generic resource deletion status

`GET /api/v1/deletion-requests/{deletion_request_id}`

Returns only the owner-visible aggregate status, safe failure code, completed target count, total target count, and timestamps. It does not expose store names, queue messages, epochs, or private target IDs.

## 4. Company analysis read model

`GET /api/v1/companies/{company_id}/analysis`

Minimum response groups:

- company identity and `analysis_status`.
- `updated_at`, `limitations`.
- summary sections with `kind`, display text, interpretation/claim classification, and safe evidence references.
- evidence entries with source label/type, published/collected times, availability/verification status, and allowed excerpt.
- conflict groups with bounded descriptions and evidence references.

Existing `GET /api/v1/companies/{company_id}/job-postings` remains the job list contract.

## Compatibility constraints

- All additions are owner-authorized FastAPI endpoints under `/api/v1`.
- Existing response fields/enums are not renamed or reinterpreted.
- All errors use `ApiErrorResponse`.
- Resource deletion reuses `deletion_requests` and `deletion_targets`; no parallel deletion ledger is allowed.
- Company analysis excludes embeddings, graph/vector keys, prompts, raw model reasoning, internal source IDs not already public, queue/worker state, and private user context.
- W3 draft restriction/replay names are not promoted into this public contract before shared adoption.
