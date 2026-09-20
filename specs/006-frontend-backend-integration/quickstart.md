# Quickstart Validation: Frontend–W1 Integration

This guide validates the completed feature. It distinguishes deterministic local/CI evidence from the separately gated live W2~W4/AWS path.

## Prerequisites

- Node.js 24 and npm.
- Python 3.10 or newer.
- Docker with Compose.
- Local Google OAuth web client for manual login validation.
- Redirect URI registered exactly as `http://localhost:8000/api/v1/auth/google/callback`.
- Authorized JavaScript/frontend origin `http://localhost:3001`.

Do not commit OAuth credentials. Use local environment files ignored by Git; use AWS Secrets Manager in deployed environments.

## 1. Start PostgreSQL

From the repository root:

```powershell
docker compose up -d postgres
docker compose ps
```

Expected: `epick-local-postgres` becomes healthy on port 5432.

## 2. Prepare and run W1 FastAPI

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Required local backend configuration after implementation:

- Google client ID and secret.
- Google callback URI.
- EPICK access-token signing key and refresh-token pepper.
- allowed frontend origin `http://localhost:3001`.
- frontend post-login URL.
- secure-cookie override for localhost only; production remains HTTPS/Secure.

Expected:

- `GET http://localhost:8000/health` returns `{"status":"ok"}`.
- `GET http://localhost:8000/health/ready` reports PostgreSQL connected.
- `http://localhost:8000/openapi.json` includes auth and existing `/api/v1` operations.

## 3. Prepare and run the frontend

In a second terminal from the repository root:

```powershell
Set-Location frontend
npm ci
npm run dev -- --port 3001
```

Required frontend configuration after implementation:

- public W1 base URL `http://localhost:8000`.
- login-start URL derived from the W1 base URL.
- Job polling interval, default 3000 ms.
- explicit synthetic/live result feature flag.

Expected: `http://localhost:3001` opens without using `epick-demo-v1` personal data as its source of truth.

## 4. Manual authentication smoke test

1. Open `http://localhost:3001` in a clean browser profile.
2. Start Google login.
3. Confirm navigation goes through W1 and Google, then returns to the allowed frontend path.
4. Confirm `/api/v1/auth/refresh` returns an EPICK access token and rotates an HttpOnly refresh cookie.
5. Confirm `/api/v1/users/me` succeeds with the in-memory bearer token.
6. Log out and verify subsequent refresh and personal API access fail with 401.

Expected security observations:

- Client secret, refresh token, and session hash never appear in browser JavaScript state or localStorage.
- Unknown `return_to`, wrong state, reused refresh token, wrong Origin, and expired session are rejected.

## 5. Deterministic API and database tests

```powershell
Set-Location backend
pytest tests/api tests/contract
pytest -m postgres tests/integration/db
ruff check app tests
```

Expected coverage includes:

- Google ID-token verifier adapter success/failure fixtures without real credentials.
- identity creation/reuse without email merge.
- refresh rotation, replay-family revocation, logout, expiry, suspended/deleting account.
- explicit credentialed CORS allow/deny behavior.
- two-owner 404 isolation.
- OpenAPI authentication/additive endpoint contracts.
- Activity/Project deletion preview and currentness behavior.

## 6. Frontend tests

After the implementation adds the test scripts:

```powershell
Set-Location frontend
npm run typecheck
npm run lint
npm run test
npm run build
```

Expected coverage includes:

- standard W1 error-envelope decoding.
- one refresh-and-retry after 401, with concurrent refresh single-flight behavior.
- Activity/Episode form mapping and server draft recovery.
- Job display mapping for every public status and dispatch status.
- polling stop/backoff/offline/visibility behavior.
- idempotency-key reuse for transport retry but not a new user intent.
- logout/delete cache generation preventing late-response repopulation.

## 7. Browser E2E

```powershell
Set-Location frontend
npm run test:e2e
```

Run against W1 with deterministic OIDC and worker fixtures. Required scenarios:

1. Two accounts see no cross-owner data.
2. Login → Activity/Episode save → Project/Question save → reload restores server state.
3. Analysis returns 202; UI separates acceptance, dispatch, execution, and terminal result.
4. WAITING_USER allows only returned actions and deduplicates double click.
5. READY recommendation permits candidate selection and reload recovery.
6. LIMITED/stale/deleted candidate remains unavailable after a late response.
7. Project deletion keeps source experience; Activity deletion follows chosen scope.
8. Logout clears personal caches and refresh cannot reuse the revoked session.

## 8. Contract drift check

The implementation must provide a deterministic command that:

1. generates W1 OpenAPI from `create_app()`;
2. generates or validates frontend TypeScript types;
3. fails if committed types/fixtures differ.

Expected: the check passes with no uncommitted generated diff.

## 9. Stage 4.5 W4 recommendation acceptance gate

Run in the dedicated synthetic acceptance environment after the W4 source revision and private schema hashes are pinned.

Required checks:

1. Create a recommendation Run through the existing W1 public endpoint while the server-side executor policy is `ENGINE`.
2. Verify PostgreSQL commits the Run, execution binding, and outbox before W1 returns 202.
3. Verify the SQS body contains opaque execution references only and the W4 worker obtains context through authenticated W1 private HTTP.
4. Execute the delivered W4 `W1ExecutionAdapter` with fixed synthetic input and test model clients; do not substitute `SyntheticRecommendationAdapter`.
5. Verify W1 atomically stores the full result/dependencies/candidates and public reads return `result_origin=ENGINE`, `LIMITED`, and explicit limitation codes.
6. Verify duplicate delivery, response loss after publish, worker restart, cancel, deletion epoch, question/snapshot revision, and Source-currentness changes create no duplicate, partial, or stale publication.
7. Force model/private-HTTP/transport failures and confirm the ENGINE Run remains a visible safe failure/retry state rather than becoming a synthetic success.
8. Verify browser network requests still target W1 only and public OpenAPI does not expose private runtime operations.

Evidence must record W1/W4 source SHA, image digest, adopted schema hashes, queue/IAM identifiers in a redacted environment manifest, test counts, and cleanup status. The earlier Question Core CT-12 report may support transport/currentness controls but cannot replace this recommendation-specific result.

This gate uses synthetic input. Passing it does not authorize REAL user content.

## 10. Live W2~W4/AWS release gate

Run only after the respective queues, identities, workers, and contracts are jointly provisioned.

Required evidence:

- W1 202 and dispatch/claim remain separate.
- real W2 partial/failure becomes the correct W1 Job state/action.
- recommendation-specific W4 output becomes usable only after W1 currentness and authorization checks and Stage 4.5 evidence is attached.
- `result_origin=ENGINE` appears only for an actual engine result.
- no browser request targets a private W2/W3/W4/Neo4j endpoint.
- REAL user content remains blocked until the recorded P2/P3 data, retention, W3 projection, and provider/model approvals are all satisfied.

Synthetic UI E2E, Question Core CT-12, and Stage 4.5 synthetic recommendation acceptance are three separate evidence classes; none alone satisfies REAL-data release.
