# Phase 0 Research: Frontend–W1 Backend Integration

## 1. Browser integration boundary

**Decision**: The browser calls only W1 public authentication endpoints and `/api/v1/*`. W2, W3, W4, SQS, Neo4j, worker lookup adapters, fences, epochs, and private contracts remain unreachable from the browser.

**Rationale**: W1 already owns authentication, owner-scoped PostgreSQL transactions, Job lifecycle, user actions, checkpoints, recommendation read models, deletion, and the public error envelope. This preserves the PRD ownership boundary and prevents private transport details from becoming a public compatibility burden.

**Alternatives considered**:

- Browser-to-W2/W3/W4 calls: rejected because it duplicates authorization and leaks private contracts.
- A new standalone BFF service: rejected because W1 already acts as the product API/control plane.

## 2. Frontend framework strategy

**Decision**: Preserve the delivered Vinext/Next/Vite React 19 application for the first integration. Add integration modules without migrating the whole UI to React Router or another framework.

**Rationale**: The delivered screens build successfully according to the handoff evidence, and a framework migration does not prove backend integration. Keeping the UI structure limits visual regressions and allows API work to proceed in vertical slices.

**Alternatives considered**:

- Immediate rewrite to React Router/Vite baseline: rejected as a separate, high-risk migration unrelated to API correctness.
- Serve the current local demo unchanged and build a second frontend: rejected because it creates two sources of UI truth.

## 3. Google OIDC and application session flow

**Decision**: Use Google OpenID Connect Authorization Code Flow with state, nonce, and PKCE. The backend owns code exchange and ID-token validation. It creates or reuses `AuthIdentity`, creates an `AuthSession`, stores only a hash of an opaque refresh token, and sends the refresh token in a `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/api/v1/auth` cookie. A refresh endpoint rotates the token family and returns a short-lived EPICK access token; the frontend holds the access token in memory and sends it as `Authorization: Bearer`.

**Rationale**:

- It preserves the existing W1 bearer-principal boundary while keeping long-lived credentials out of JavaScript storage.
- The existing `auth_identities` and `auth_sessions` schema already supports provider subjects, refresh-token hashes, token-family rotation, expiry, and revocation.
- Google requires an exact redirect URI and server-side validation of signature, issuer, audience, and expiry; its OIDC documentation recommends using a maintained library rather than custom cryptography.
- `SameSite` is defense in depth, not a complete CSRF control. Refresh/logout also validate allowed `Origin`/`Referer`; OAuth callback validates one-time state, nonce, and PKCE.

**Alternatives considered**:

- Store Google or EPICK tokens in `localStorage`: rejected due to XSS credential exposure.
- Send the Google ID token as the long-lived bearer on every API call: rejected because EPICK cannot centrally rotate/revoke its own sessions cleanly and Google tokens are not an application session contract.
- Pure server session cookie for every API request: viable, but rejected for this phase because it would replace the existing bearer dependency and require CSRF protection across every mutation. The chosen split limits credentialed-cookie use to auth endpoints.

**References**:

- Google web-server OAuth: https://developers.google.com/identity/protocols/oauth2/web-server
- Google OpenID Connect and ID-token validation: https://developers.google.com/identity/openid-connect/openid-connect
- OWASP session management: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html

## 4. Identity mapping and account creation

**Decision**: Use `(provider="google", provider_subject=sub)` as the unique identity key. Never merge accounts by email. On first valid login, create one `User` and `AuthIdentity`; on later login, update safe profile metadata and `last_login_at` without changing ownership.

**Rationale**: Google documents `sub` as stable and never reused, while email can change. The existing `IdentityService` explicitly avoids email-based account merging.

**Alternatives considered**:

- Email as the owner key: rejected because it is mutable and unsafe for account merging.
- Client-provided `user_id`: rejected by PRD and common baseline CB-01.

## 5. CORS and browser security boundary

**Decision**: Configure FastAPI `CORSMiddleware` from an environment-specific explicit origin allowlist. Permit credentials only for auth cookie calls; explicitly allow required methods and headers (`Authorization`, `Content-Type`, `Idempotency-Key`, `If-Match`, `X-CSRF-Token`) and expose only required response headers (`Location`, `ETag`, `Retry-After`, `X-Correlation-ID`). Production origins and redirect URIs require HTTPS.

**Rationale**: FastAPI documents that credentialed CORS cannot use wildcard origins, methods, or headers. Explicit configuration also makes local port and production subdomain differences testable.

**Alternatives considered**:

- `allow_origins=["*"]`: rejected because it is incompatible with credentialed requests and too permissive.
- Disable CORS by proxying all requests through framework server routes: deferred; it ties the API to the frontend hosting implementation and does not remove the need for direct API tests.

**Reference**: https://fastapi.tiangolo.com/tutorial/cors/

## 6. API client and type contract

**Decision**: Generate TypeScript types from W1 FastAPI OpenAPI, wrap native `fetch` in one `frontend/lib/api` client, and use TanStack Query for server cache, polling, mutation invalidation, and request cancellation. Do not use Axios unless a concrete missing capability is found.

**Rationale**:

- FastAPI already generates the authoritative public schema and has OpenAPI regression tests.
- Native fetch covers bearer headers, cookie credentials on auth calls, abort signals, and structured error decoding without a second transport abstraction.
- TanStack Query directly addresses the frontend's missing server-state cache and stale/late-response handling.

**Alternatives considered**:

- Hand-maintained duplicate TypeScript DTOs: rejected because drift would be detected late.
- Keep all state in React Context/useState: rejected because it lacks normalized server cache, mutation invalidation, and robust polling behavior.
- Axios plus React Query: viable, but Axios adds no required capability for the current contract.

## 7. Job polling and user-action behavior

**Decision**: Poll only W1 `GET /api/v1/jobs/{job_id}`. Default active interval is configurable and starts at 3 seconds. Pause while offline or the document is hidden; back off network failures up to 15 seconds; resume with an immediate current-state read. Stop on terminal states. Stop automatic polling for `WAITING_USER`; for `PAUSED_RATE_LIMIT`, respect `retry_after_seconds` and require explicit user retry/action. Never cause a mutation from a polling callback.

**Rationale**: This satisfies the MVP without introducing SSE/WebSocket infrastructure and preserves the distinction between observing a Job and rerunning it.

**Alternatives considered**:

- SSE/WebSocket: deferred until polling load or latency evidence justifies it.
- Fixed aggressive polling in all states: rejected due to unnecessary load and incorrect behavior while waiting for user decisions.

## 7A. Current `Experience` UI to Activity/Episode mapping

**Decision**: Treat each current experience card as one Activity and its "기억에 남는 사건" narrative as an initial Episode. Saving a new completed experience is a resumable two-step flow: create/update Activity, then create/update its Episode, then complete the records when validation passes. If the second request fails, keep the Activity as a server-side draft and surface it through Home/Resume rather than rolling it back client-side.

**Rationale**: The backend intentionally separates reusable Activity metadata from one-or-more Episodes used by recommendation candidates. Flattening them into a frontend-only DTO would discard version and candidate semantics. A server draft gives safe recovery across network failure.

**Alternatives considered**:

- Put the entire story only in `Activity.original_narrative`: rejected because recommendation candidates reference Episode versions.
- Add a special composite endpoint solely for the current form: deferred; the existing two APIs already preserve drafts and idempotency, and a composite endpoint can be added later only if UX evidence requires atomic creation.

## 8. Mutation idempotency, concurrency, and late responses

**Decision**: Generate one UUID idempotency key per user intent and reuse it only for transport retries of the same canonical request. Send `If-Match` or explicit expected versions where the existing endpoint requires them. Cancel obsolete queries with `AbortController`, compare server versions/`updated_at`, and gate cache writes after delete/logout with a client generation token.

**Rationale**: W1 already implements idempotency and version conflict responses. Client safeguards prevent duplicate clicks and out-of-order responses from reintroducing stale/deleted data.

**Alternatives considered**:

- New idempotency key for every retry: rejected because it defeats server deduplication.
- Optimistic permanent deletion without server reconciliation: rejected because a failed request must be reconciled against current authority.

## 9. Existing public API versus additive gaps

**Decision**: Reuse current Activity/Episode, Home/Resume, Project/Question, Job, Recommendation, Selection, Preferences, Notification, and Account Deletion APIs. Add only:

1. Google auth start/callback/refresh/logout surface.
2. Activity and application-project deletion preview/request/status surfaces, because the UI exposes these actions but the current routers expose no corresponding deletion endpoint. Reuse the existing generic `deletion_requests`/`deletion_targets` ledger rather than inventing a second deletion store.
3. Any company analysis/evidence read model proven missing after UI-to-OpenAPI mapping.
4. Optional cache headers/ETag or response headers when required by an existing version contract.

Do not add public W3 restriction/replay enums until the shared contract is adopted.

**Rationale**: Most product operations already exist. The frontend handoff document proposed names but did not establish a competing contract.

**Alternatives considered**:

- Implement the frontend proposal DTOs as a second API: rejected because it duplicates and can contradict the approved W1 public API.

## 10. Testing strategy

**Decision**:

- Backend: pytest unit/API tests, OpenAPI contract tests, and PostgreSQL integration tests for identity/session rotation, owner isolation, CORS, logout, and deletion invalidation.
- Frontend: Vitest + React Testing Library for components/hooks, Mock Service Worker for public API fixtures, and Playwright for browser E2E.
- Contract drift: generate/check TypeScript definitions from a deterministic W1 OpenAPI artifact in CI.
- E2E modes: synthetic W1 worker results for deterministic CI; separately gated live W2~W4/AWS E2E.

**Rationale**: The constitution requires tests for functional changes and real PostgreSQL checks across persistence boundaries. Deterministic synthetic tests must not be confused with live engine evidence.

**Alternatives considered**:

- Only manual browser testing: rejected because state, auth, deletion, and retry regressions are not reliably reproducible.
- Require live W2~W4 for every frontend CI run: rejected because it makes public API integration nondeterministic and couples tests to external infrastructure.

## 11. Deployment configuration

**Decision**: Keep all environment-specific values outside source:

- Backend: Google client ID/secret, OIDC redirect URI, JWT signing key, refresh-token pepper, allowed frontend origins, frontend post-login URL, cookie security mode, session/access TTLs.
- Frontend: public W1 base URL, Google login start URL, configurable Job polling interval, feature flags distinguishing synthetic from live engine paths.
- Production secrets belong in AWS Secrets Manager; non-secret URLs/origins belong in deployment environment configuration.

**Rationale**: OAuth secrets and signing material must not enter Git or browser bundles; URLs vary by local/staging/production.

**Alternatives considered**:

- Hard-code localhost or AWS URLs: rejected because it breaks deployment parity and risks secret leakage.

## 12. W4 recommendation execution boundary and evidence

**Decision**: Insert US4.5 between recommendation UI/selection and deletion/company work. Keep the existing W1 public recommendation API unchanged and use a hybrid private runtime:

1. W1 freezes an ENGINE Run and commits an opaque execution reference to its PostgreSQL outbox.
2. The existing relay/SQS pattern delivers the at-least-once trigger to W4.
3. W4 implements its existing `RunStore` seam through authenticated W1 private HTTP for acquire, context load, authorization, publication, and failure.
4. W1 alone owns PostgreSQL transactions and maps the W4 publication into the existing public Run/Candidate read model.

**Rationale**:

- `backend/app/services/recommendation_execution.py` already defines `RecommendationExecutionPort.execute(owner_user_id, run_id)`; W4's delivered `epick_w4/w1_bridge.py` implements that exact shape and defines the missing host responsibilities.
- The W4 bridge freezes a lease and context hash, releases the transaction during model calls, rechecks authorization/currentness, maps W4 candidate states to existing W1 states, and requires atomic publication. Reusing that seam is lower-risk than inventing a browser or public W4 API contract.
- `md/w4/W1_W4_CT12_Actual_Runtime_Result_2026-09-20.md` proves the actual Question Core W4→SQS→W1 runtime, including duplicate, response-loss, restart, cancel, deletion-epoch, and relation-change behavior. These are valid transport/currentness patterns for the new path.
- That CT-12 does **not** execute the W4 recommendation pipeline. The W4 bridge itself names its publication schema `0.1-draft`, accepts only `SYNTHETIC` context, and returns `W4_POLICY_REVIEW_PENDING` plus `W4_MODEL_NOT_PRODUCTION_SELECTED`. A recommendation-specific acceptance run is therefore mandatory.
- Queue delivery decouples long model work and gives retry/DLQ behavior. Private HTTP keeps W1 as the policy and PostgreSQL authority and avoids sharing DB credentials with W4. Queue bodies carry references only, while current data is fetched at execution time and fenced again at publication.

**Origin and data-kind rule**:

- `result_origin=ENGINE` means actual W4 recommendation code executed and W1 accepted its fenced publication.
- It does not mean the input was REAL, the model is production-approved, or the result is unrestricted. Initial W4 acceptance results are `ENGINE` plus `LIMITED` because the input is fixed synthetic data and product/model policy is pending.
- A requested ENGINE Run remains ENGINE through failure. It must never be completed by `SyntheticRecommendationAdapter` as a fallback.

**Alternatives considered**:

- Browser calls W4 directly: rejected because it leaks private contracts and duplicates W1 authentication/currentness.
- W4 gets direct PostgreSQL credentials: rejected because it breaks W1 ownership, RLS/session assumptions, and transaction accountability.
- Synchronous W1→W4 HTTP for the entire model call: rejected because a long request couples user latency and retries to model execution and makes response-loss handling harder.
- Treat the Question Core CT-12 as sufficient and enable immediately: rejected because its producer, payload, direction, and persistence effect differ from recommendation publication.
- Copy W4 output into candidates without storing full result/dependencies: rejected because Source invalidation, auditability, and result-version integrity would be lost.

## Resolved gates and remaining deployment inputs

There are no unresolved architecture clarifications. The following are deployment or approval gates and must not be invented during implementation:

- Google OAuth client ID and client secret.
- Exact staging/production frontend origin and backend callback URI.
- AWS Secrets Manager secret identifiers and deployed public hostnames.
- Exact W4 image/source revision and private contract hashes selected for the Stage 4.5 acceptance environment.
- Explicit switch date from W1 synthetic adapter to W4 ENGINE results after the recommendation-specific joint E2E evidence.
- P2/P3 REAL-data policy, retention/erasure behavior, W3 projection, and provider/model approval. Until recorded, W4 accepts synthetic input only.
