# Implementation Plan: Frontend–W1 Backend Integration

**Branch**: `006-frontend-backend-integration` *(Spec Kit feature identifier; worktree remains on `develop`)* | **Date**: 2026-09-20 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/006-frontend-backend-integration/spec.md`

## Summary

Connect the delivered EPICK React UI to the existing W1 FastAPI public API without exposing W2, W3, W4, SQS, Neo4j, or worker contracts to the browser. Implement Google OIDC Authorization Code authentication with server-controlled refresh sessions, explicit credentialed CORS, an OpenAPI-derived TypeScript client, Activity/Episode and Project/Question persistence, W1 Job polling/actions, recommendation/selection flows, resource deletion, and missing company-analysis read models. Preserve current public contracts and test each backend, PostgreSQL, frontend, and browser boundary.

The integration proceeds as vertical stages. Deterministic W1 synthetic flows establish the public UI contract first; Stage 4.5 then connects the actual W4 recommendation execution seam through W1-owned queue and private HTTP boundaries before deletion/company work. A successful synthetic UI path or the separate W1↔W4 Question Core CT-12 must never be reported as evidence that the recommendation publication path or REAL-data policy is complete.

## Technical Context

**Language/Version**: Python >=3.10; TypeScript 5.9.3; Node.js >=22.13.0 (handoff validated with Node 24); React 19.2.6

**Primary Dependencies**: FastAPI >=0.141.1, Pydantic Settings, SQLAlchemy 2, psycopg 3, Alembic, Authlib (OIDC/OAuth/JOSE; version pinned during implementation), httpx; current Vinext 1 beta/Next 16/Vite 8 frontend; TanStack Query, openapi-typescript, Vitest, React Testing Library, MSW, Playwright

**Storage**: PostgreSQL 16 system of record; existing `users`, `auth_identities`, `auth_sessions`, workspace, Job, recommendation, selection, and deletion tables, plus the minimum ENGINE execution lease/publication/dependency state proven necessary in Stage 4.5. Browser persistent storage is not authoritative and stores no credentials or personal API payloads.

**Testing**: pytest 9, pytest-asyncio, PostgreSQL integration markers, OpenAPI/JSON Schema contract tests, Ruff; Vitest + React Testing Library + MSW; Playwright browser E2E; separately gated live W2~W4/AWS E2E

**Target Platform**: Modern desktop/mobile browsers; FastAPI on Linux/AWS runtime; local Windows development with Docker PostgreSQL; HTTPS required outside localhost

**Project Type**: Web application with separate frontend and W1 API/control plane

**Performance Goals**: W1 owner-scoped CRUD and Job reads p95 <=300 ms in staging excluding engine work; visible Job state refresh within one configured 3-second polling interval; user mutation feedback starts within 100 ms; token refresh is single-flight per browser context

**Constraints**: Frontend calls W1 only; FastAPI backend; PostgreSQL authority; existing public contracts preserved; no secrets/credentials/private payloads in source or logs; explicit credentialed CORS; no access/refresh tokens in localStorage; 202 means durable acceptance only; polling never triggers execution; W3 draft restriction/replay names remain gated; ENGINE selection is server-controlled; no silent ENGINE→SYNTHETIC fallback; REAL W4 input remains disabled until policy/model approval

**Scale/Scope**: One delivered frontend, approximately five primary views; existing W1 v1 routers; Google as the only v1 login provider; Activity/Episode, Project/Question, Job, Recommendation/Selection, settings/consent, deletion, and company-analysis view; user Job concurrency remains the existing maximum of three executing Jobs

## Constitution Check

*GATE: Passed before Phase 0 research.*

| Principle | Plan compliance | Gate |
|---|---|---|
| I. FastAPI Backend Standard | New auth, deletion, and company surfaces use router/schema/service/repository boundaries. OAuth verification and session rotation do not live solely in handlers. | PASS |
| II. PostgreSQL Is the System of Record | Existing identity/session/deletion tables remain authoritative; any schema change requires Alembic and PostgreSQL tests. Browser state is a cache only. | PASS |
| III. Tests Required | Each vertical stage includes API/service/PostgreSQL tests plus frontend adapter/component/E2E coverage. Fixtures contain synthetic content and no secrets. | PASS |
| IV. Existing Contracts Preserved | Existing `/api/v1` schemas/enums stay unchanged; auth/resource deletion/company analysis are additive. W3 draft fields are not promoted. | PASS |
| Technology constraints | FastAPI, PostgreSQL, existing AWS/private boundaries, and deployment-provided OAuth values are respected. | PASS |
| Workflow/quality gates | PRD FR-01/02/13/19-25/28-30 and invariants are mapped in spec/contracts; live external gates are explicitly separated. | PASS |

No justified constitution violation is required. The inherited constitution ratification-date TODO is governance metadata and does not change this feature's gates.

## Project Structure

### Documentation (this feature)

```text
specs/006-frontend-backend-integration/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── auth.openapi.yaml
│   ├── frontend-w1-mapping.md
│   ├── additive-public-api.md
│   └── w1-w4-recommendation-runtime.md
└── tasks.md                 # generated later by speckit-tasks
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── api/
│   │   ├── dependencies.py          # EPICK bearer principal validation
│   │   ├── schemas/                 # auth/additive public DTOs
│   │   └── v1/                      # auth, existing product, additive routes
│   ├── core/config.py               # OIDC/CORS/session settings
│   ├── models/identity.py            # existing User/AuthIdentity/AuthSession
│   ├── repo/                         # identity/session/read-model persistence
│   └── services/                     # OIDC, session, deletion, read-model logic
├── migrations/versions/              # only if an actual schema gap is proven
├── contracts/                         # W1 authoritative public/private schemas
└── tests/
    ├── api/
    ├── contract/
    ├── integration/db/
    └── services/

frontend/
├── app/                               # delivered screens and providers
├── components/
├── lib/
│   ├── api/                           # typed fetch client/error/auth modules
│   ├── queries/                       # TanStack Query options/hooks
│   └── state/                         # ephemeral auth and UI state only
├── tests/
│   ├── unit/
│   ├── component/
│   └── e2e/
├── generated/                         # deterministic OpenAPI TypeScript types
└── package.json

f/
└── docs/                              # original frontend handoff evidence only
```

**Structure Decision**: Keep the existing `backend/` FastAPI and `frontend/` application roots. Do not add a BFF or a second frontend. `f/` is evidence/reference and is not imported by runtime code.

## Phase 0: Research Outcome

Research decisions are recorded in [research.md](./research.md). All technical unknowns are resolved. OAuth credentials, deployed origins/redirect URI, and AWS secret identifiers remain deployment inputs, not implementation ambiguities.

Key decisions:

1. Browser → W1 only.
2. Preserve the current frontend framework during integration.
3. Google Authorization Code + PKCE; W1 exchanges and validates; opaque refresh cookie plus in-memory short-lived EPICK bearer.
4. Reuse existing `AuthIdentity`/`AuthSession`; never merge by email.
5. Explicit origin CORS; no wildcard with credentials.
6. OpenAPI-derived TypeScript types, native fetch, TanStack Query.
7. Configurable 3-second W1 Job polling with stop/backoff rules.
8. Existing API first; additive auth, Activity/Project deletion, and company-analysis surface only.
9. W4 recommendation execution uses a hybrid private boundary: W1 durable outbox/SQS carries an opaque Run trigger; authenticated private HTTP implements W4's `RunStore` operations without granting W4 direct PostgreSQL ownership.
10. The 2026-09-20 CT-12 result is reusable evidence for SQS delivery/currentness patterns, but it covers Question Core rather than recommendation execution; Stage 4.5 therefore requires its own W4 recommendation publication acceptance run.

## Phase 1: Design Outcome

The detailed entities, lifecycle rules, and cache safety model are in [data-model.md](./data-model.md). Interface contracts are in [contracts/](./contracts/), and validation steps are in [quickstart.md](./quickstart.md).

### Stage 0 — Contract freeze and test harness

**Backend**:

- Export deterministic W1 OpenAPI and preserve existing contract tests.
- Add the authentication contract and document additive deletion/company surfaces.
- Create synthetic OIDC and Job fixtures; no real secret in tests.

**Frontend**:

- Add typecheck, unit/component, and Playwright test harnesses.
- Generate TypeScript API types from W1 OpenAPI and fail CI on drift.
- Add environment parsing for W1 base URL and explicit synthetic/live marker.

**Exit criteria**:

- Existing backend tests remain green.
- Generated types reproduce with no diff.
- Empty frontend test/build pipeline passes.
- No frontend source contains private W2~W4 URLs/contracts.

### Stage 1 — Google OIDC, sessions, principal, and CORS

**Backend**:

- Add OIDC/session/CORS settings with production validation.
- Implement Google start/callback using state, nonce, PKCE, exact redirect URI, and a maintained verifier.
- Add identity lookup/create, refresh-token hashing/rotation/reuse detection, access-token signing, refresh, logout, and current-session validation.
- Replace fail-closed production principal dependency with EPICK bearer validation while retaining dependency overrides for tests.
- Explicit credentialed CORS and allowed response headers.

**Frontend**:

- Add login/logout/callback bootstrap.
- Keep access token in memory; single-flight refresh; one retry after 401.
- Clear cache on logout/account change.

**Exit criteria**:

- Two-account isolation, invalid token/state/origin, refresh replay, logout, and deletion-session revocation tests pass against PostgreSQL.
- No credential appears in localStorage, logs, OpenAPI examples, or bundles.

### Stage 2 — Read client and Activity/Episode integration

**Backend**:

- No contract changes unless OpenAPI-to-screen mapping proves a missing safe field.
- Verify pagination, If-Match/version, idempotency, and 404 behavior through API tests.

**Frontend**:

- Install Query provider and common typed client/error boundary.
- Connect `/users/me`, `/home`, `/resume-items`.
- Replace Experience local state with Activity/Episode list/detail/create/update/complete.
- Map one current form experience to Activity plus initial Episode; preserve partial server draft on failure.

**Exit criteria**:

- Refresh/browser restart recovers server data.
- Two-step experience save is resumable and double-click safe.
- Current local demo seed is available only under an explicit development fixture flag.

### Stage 3 — Project/Question and Job control integration

**Backend**:

- Reuse project/question/job routes and public Job contract.
- Add missing response headers only where current version/idempotency semantics require them.

**Frontend**:

- Connect project and question create/update/list/detail/version flows.
- Replace timer state machine with W1 Job creation/read polling.
- Implement the mapping in `frontend-w1-mapping.md` without inventing a second persisted status enum.
- Connect required action, retry, and cancel with expected versions and stable idempotency keys.

**Exit criteria**:

- 202/dispatch/running/terminal states are never conflated.
- WAITING_USER and PAUSED_RATE_LIMIT do not auto-run.
- Offline, hidden-tab, out-of-order response, and double-submit tests pass.

### Stage 4 — Recommendation and material selection

**Backend**:

- Reuse recommendation run/candidate/selection APIs and preserve `result_origin`.
- Verify stale/limited/current selection gates with contract and service tests.

**Frontend**:

- Connect recommendation creation/status/candidate details.
- Display match status, evidence reason, strength, limitation, validation, and origin.
- Connect compare/select/replace/clear and invalidate only affected query keys.

**Exit criteria**:

- READY and allowed LIMITED candidates behave correctly.
- stale/restricted/deleted results cannot be selected or restored by late cache writes.
- synthetic output remains visibly distinct from ENGINE output.

### Stage 4.5 — W4 recommendation execution integration

**Evidence baseline**:

- `md/w4/W1_W4_CT12_Actual_Runtime_Result_2026-09-20.md` proves actual W4 Question Core producer → SQS → W1 consumer delivery, duplicate handling, response-loss recovery, restart behavior, and W1 currentness fences.
- W4 `epick_w4/w1_bridge.py` already implements W1's `RecommendationExecutionPort.execute(owner_user_id, run_id)` shape and defines acquire/load/authorize/publish/fail responsibilities, candidate-state mapping, and atomic publication requirements.
- The same W4 source labels `w4-w1-publication/0.1-draft`, rejects non-synthetic context, and records policy/model limitations. Therefore neither the Question Core CT-12 nor the local bridge tests authorize REAL user data or prove the recommendation runtime path.

**Contract freeze**:

- Pin the exact W4 source revision and hashes for service input, C01 server context/output, and publication candidate contracts before code integration.
- Adopt the minimum private W1↔W4 runtime contract described in `contracts/w1-w4-recommendation-runtime.md`; keep it outside public OpenAPI and browser-generated types.
- Preserve the public `POST /api/v1/questions/{question_id}/recommendation-runs` request/response. The client cannot choose provider, model, W4 URL, owner, or execution origin.

**W1 control plane and persistence**:

- Add a server-selected execution policy: `SYNTHETIC` remains the deterministic development/test adapter; `ENGINE` creates a run-bound execution binding and durable outbox trigger. Never fall back from a failed ENGINE Run to synthetic candidates.
- Implement short PostgreSQL transactions for acquire and publish. Bind owner, Run, question version, snapshot, EpisodeVersion mapping, execution lease, deletion epoch, context hash, and Source/currentness revisions; release locks before model work.
- Store W4 detailed result, Source dependencies, result version, mapped candidates, and terminal Run/result state atomically. Recheck all fences on publish and on stored-result use.

**Private runtime**:

- Publish only opaque Run/owner/contract references through W1 outbox → SQS; no Episode text, company evidence, credentials, or model configuration is placed in queue bodies.
- The W4 worker consumes at-least-once triggers and implements its `RunStore` calls through authenticated W1 private HTTP: acquire, load context, authorize at PROCESS/SEND_TO_PROVIDER/RETURN_TO_CALLER, publish, and fail.
- Reuse the validated CT-12 IAM, replay, retry/DLQ, response-loss, restart, and currentness patterns, but give recommendation commands a separate schema/version and evidence record.

**Deployment and AWS boundary**:

- Do not reuse the existing W4 Question Core Main Queue, DLQ, sender role, bearer, env file, or
  evidence directory. Recommendation execution has the opposite transport direction and its own
  message contract: W1 relay sends, while a dedicated W4 recommendation workload receives.
- Do not provision AWS resources until the private schemas, worker entrypoint, queue routing, image
  recipes, migrations, and mutation-free preflight pass local and CI validation. Resource names,
  ARNs, URLs, credentials, and provider/model secrets remain deployment inputs rather than source
  constants.
- Provision one encrypted recommendation-execution Main Queue and DLQ with bounded redrive. Grant
  W1 only `SendMessage`; grant the dedicated W4 workload only receive/delete/change-visibility and
  queue-attribute reads. Do not grant the W4 workload W1 PostgreSQL credentials, Secrets Manager
  wildcard access, queue purge, or W1 control-plane permissions.
- A shared EC2 instance profile is not sufficient evidence of W4 least privilege. The deployed W4
  process must use a distinct task/workload identity or a narrowly scoped assumed role whose stable
  identity is recorded by preflight.
- Store the W4 private-HTTP credential and any approved test-model credential in separate Secrets
  Manager entries. Queue URLs, private base URL, contract revisions, and timeouts are non-secret
  root-owned runtime configuration. The browser and public API environment never receive them.
- Keep the W1 private RunStore surface off the public listener. On a shared private host it is
  reachable only through the approved container network; on separate compute, the security group
  permits only the W4 workload to the private port. W4 has no direct RDS network or login path.
- Deploy both W1 and W4 by immutable image manifest digest, apply the reviewed W1 Alembic migration
  and runtime grants first, then run a mutation-free two-sided preflight before enabling ENGINE in
  the isolated acceptance environment.
- Derive SQS visibility and heartbeat from the bounded W4 execution timeout rather than copying the
  Question Core value. Monitor visible/in-flight/oldest-message age, DLQ depth, private-HTTP failures,
  lease expiry, and failed publication without logging payload content.
- Rollback disables ENGINE admission first, leaves accepted ENGINE Runs observable as ENGINE
  failures/retryable states, drains or quarantines the dedicated queue according to the runbook, and
  never completes those Runs through `SyntheticRecommendationAdapter`.

**Tests and rollout**:

- Contract/hash tests, service/repository tests, PostgreSQL race tests, queue/relay tests, private HTTP authorization tests, and W4 adapter integration tests use fixed synthetic content and fake model clients.
- Browser E2E still calls W1 only and proves that an actual W4 code execution is shown as `ENGINE`; because input/policy/model remain non-production, the same result is also `LIMITED` with explicit limitations.
- The feature flag defaults to synthetic/off outside the dedicated acceptance environment. REAL user input remains disabled until P2/P3 policy, retention, provider/model, and W3 projection approvals are recorded.

**Execution order**:

1. Freeze W1↔W4 private schemas and source/hash provenance.
2. Implement and verify W1 persistence, short transactions, private HTTP, outbox routing, and the W4
   HTTP RunStore/SQS worker locally.
3. Pass API/contract/service/PostgreSQL/runtime tests and CI with ENGINE still default-off.
4. Produce immutable W1/W4 images and deployment Compose/env/policy templates.
5. Apply the W1 migration/runtime grants, create the isolated Queue/DLQ, bind least-privilege IAM,
   inject secrets, and verify private network reachability.
6. Run mutation-free preflight, then the recommendation-specific synthetic AWS acceptance matrix.
7. Enable staging ENGINE admission only after evidence reconciliation and cleanup; keep REAL disabled.

**Exit criteria**:

- Actual W4 recommendation code—not the synthetic W1 adapter and not only Question Core—executes from a W1-accepted Run and publishes through W1.
- Normal, duplicate, response-loss, worker restart, cancel, deletion epoch, question/snapshot change, and Source-currentness cases produce no duplicate, partial, or stale publication.
- `result_origin=ENGINE` is set only after actual W4 execution and successful fenced publication; failures remain failures and never become synthetic successes.
- Public OpenAPI is unchanged except already approved additive surfaces, browser network destinations remain W1-only, and REAL-data enablement remains explicitly blocked.

### Stage 5 — Resource deletion and company-analysis gaps

**Backend**:

- Extend the existing deletion ledger/service to Activity and ApplicationProject preview/confirmation/status flows.
- Preserve source experiences on Project deletion; apply Activity scope exactly as previewed.
- Add company analysis/evidence/conflict read model using W1-safe projections only.

**Frontend**:

- Replace local delete operations with preview/confirm/status UX.
- Quarantine query keys immediately after confirmation and reconcile terminal state.
- Replace company mock sections with public W1 read models; retain a clear development fixture flag for unavailable live data.

**Exit criteria**:

- Project and Activity deletion acceptance scenarios pass, including late responses and session/account deletion interaction.
- Restricted evidence and internal graph/worker identifiers never appear.

### Stage 6 — Full regression, deployment, and live-engine gate

- Run backend unit/API/contract/PostgreSQL tests and lint.
- Run frontend typecheck/lint/unit/component/build/Playwright.
- Validate local Google login, then staging OAuth/CORS/cookie behavior over HTTPS.
- Deploy configuration via AWS environment/Secrets Manager; never bake credentials into images.
- Re-run the separately approved W2~W4/AWS E2E, including the Stage 4.5 recommendation publication path, before enabling any live feature flag. Question Core CT-12 evidence is referenced separately.
- Preserve rollback: auth/API deployment and frontend feature flag can revert independently; database changes, if any, use Alembic downgrade/forward validation.

**Exit criteria**:

- [quickstart.md](./quickstart.md) passes.
- Contract drift is zero.
- Synthetic and live evidence are reported separately.
- Browser network inventory shows only W1 public destinations.

## Dependency Order

```text
Stage 0 Contract/Test Baseline
          │
          ▼
Stage 1 Auth/CORS
          │
          ▼
Stage 2 Home + Activity/Episode
          │
          ▼
Stage 3 Project/Question + Job
          │
          ▼
Stage 4 Recommendation/Selection
          │
          ▼
Stage 4.5 W4 Recommendation Runtime
          │
          ▼
Stage 5 Deletion + Company Analysis
          │
          ▼
Stage 6 Deployment + Live W2~W4 Gate
```

Backend contract tests and frontend fixtures for the next stage may be prepared in parallel only after the preceding stage's public contract is frozen.

## Risk Controls

| Risk | Control |
|---|---|
| OAuth secret or token leakage | Backend-only secret config, redacted logging, bundle/log scanning, HttpOnly refresh cookie |
| Email-based account collision | Unique Google `sub`; no email merge |
| Refresh-token replay | Hash-only storage, rotation family, reuse detection and family revocation |
| Credentialed CORS exposure | Exact origin allowlist; explicit headers/methods; Origin validation on auth cookie endpoints |
| Frontend/backend contract drift | Deterministic OpenAPI-derived types and CI diff |
| Duplicate user mutations | Per-intent idempotency key reused only for identical transport retry |
| Late response restores stale/deleted data | Abort signals, version checks, query generation fences, deletion quarantine |
| Synthetic data mistaken for live engine | `result_origin`, explicit fixture flag, separate live gate |
| Question Core CT-12 mistaken for recommendation proof | Separate evidence matrix and a dedicated W4 recommendation Run→publication acceptance test |
| W4 gains direct PostgreSQL authority | Queue carries opaque references; W1 private HTTP owns authorization and every transaction |
| ENGINE failure silently returns synthetic output | Server-selected origin is immutable per Run; no fallback; contract and E2E assertions |
| REAL user content sent before approval | W4 bridge synthetic allowlist, default-off feature flag, explicit P2/P3/model/retention gate |
| W3 draft becomes accidental public contract | No draft enum/field in public schemas until joint adoption |
| Framework migration destabilizes UI | Preserve current stack; make migration a separate feature |

## Post-Design Constitution Re-check

*GATE: Passed after Phase 1 design.*

- FastAPI boundaries are explicit in the auth/additive contract and source layout.
- PostgreSQL remains authoritative; existing identity/session/deletion models are reused.
- Every functional slice has automated backend/frontend/integration coverage and synthetic fixtures contain no secrets.
- Existing public Job/error/workspace contracts are reused; new endpoints are additive and require OpenAPI compatibility tests.
- The W4 draft is not exposed publicly: Stage 4.5 pins and adopts only a versioned private seam, keeps REAL disabled, and requires a recommendation-specific live gate.

Result: **PASS — implementation may proceed to task generation.**

## Complexity Tracking

No constitution violation or exceptional structural complexity is introduced.
