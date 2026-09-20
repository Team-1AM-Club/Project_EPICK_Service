# Tasks: Frontend–W1 Backend Integration

**Input**: Design documents from `/specs/006-frontend-backend-integration/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required by the feature specification and constitution. Within every user story, write the listed tests first and confirm they fail for the intended reason before implementation.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated as an independent increment. All browser data traffic must terminate at W1 public endpoints.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelizable because it targets different files and has no dependency on another incomplete task in the same phase.
- **[Story]**: User story traceability label.
- Every task names its primary file path.

## Phase 1: Setup — Contract and Test Tooling

**Purpose**: Add reproducible backend/frontend contract generation and frontend test tooling without changing product behavior.

- [X] T001 Add Authlib and runtime httpx dependencies for OIDC to `backend/pyproject.toml`
- [X] T002 Add TanStack Query, openapi-typescript, Vitest, React Testing Library, MSW, Playwright, typecheck, test, and E2E scripts to `frontend/package.json` and refresh `frontend/package-lock.json`
- [X] T003 [P] Configure jsdom unit/component tests in `frontend/vitest.config.ts` and shared setup in `frontend/tests/setup.ts`
- [X] T004 [P] Configure deterministic Chromium E2E projects and local web servers in `frontend/playwright.config.ts`
- [X] T005 [P] Implement deterministic FastAPI OpenAPI export with sorted stable JSON in `backend/scripts/export_openapi.py`
- [X] T006 Implement OpenAPI TypeScript generation and no-diff verification in `frontend/scripts/generate-api-types.mjs`
- [X] T007 [P] Document non-secret backend OIDC/CORS/session variables with safe placeholders in `backend/.env.example`
- [X] T008 [P] Document frontend W1 URL, polling interval, and synthetic/live flags in `frontend/.env.example` and `frontend/cloudflare-env.d.ts`

**Checkpoint**: Dependency installation, empty Vitest/Playwright discovery, and deterministic OpenAPI export work on a clean checkout.

---

## Phase 2: Foundational — Typed Client, Cache, Fixtures, and Guardrails

**Purpose**: Establish shared integration infrastructure that blocks every user story.

**⚠️ CRITICAL**: Do not start story implementation until this phase is complete.

- [X] T009 Write the backend OpenAPI reproducibility and public-error-envelope regression tests in `backend/tests/contract/test_frontend_openapi_export.py`
- [X] T010 Generate and commit the baseline W1 TypeScript surface in `frontend/generated/w1-api.d.ts`
- [X] T011 [P] Write frontend environment validation tests in `frontend/tests/unit/api-config.test.ts`
- [X] T012 [P] Write public error decoding, abort, 401 retry limit, and forbidden private destination tests in `frontend/tests/unit/api-client.test.ts`
- [X] T013 Implement validated public runtime configuration in `frontend/lib/api/config.ts`
- [X] T014 Implement the typed native-fetch client, `ApiErrorResponse` decoding, bearer injection, abort support, and one-refresh retry hook in `frontend/lib/api/client.ts`
- [X] T015 [P] Implement per-intent UUID idempotency key creation and canonical retry reuse in `frontend/lib/api/idempotency.ts`
- [X] T016 [P] Implement QueryClient defaults, personal query-key factories, and client-generation fencing in `frontend/lib/queries/query-client.ts`
- [X] T017 [P] Create shared MSW public API handlers and synthetic fixtures in `frontend/tests/mocks/handlers.ts` and `frontend/tests/mocks/server.ts`
- [X] T018 Mount QueryClient and error/toast boundaries without changing current screen behavior in `frontend/app/providers.tsx` and `frontend/app/layout.tsx`
- [X] T019 Add CI jobs for backend OpenAPI drift and frontend typecheck/unit/build/E2E discovery in `.github/workflows/frontend-backend-integration.yml`

**Checkpoint**: The client can call only configured W1 HTTP origins, decode the current W1 error envelope, cancel obsolete requests, and fail CI on OpenAPI type drift.

---

## Phase 3: User Story 1 — Google Login and Private Workspace Recovery (Priority: P1) 🎯 Technical MVP

**Goal**: Authenticate with Google, establish a revocable EPICK session, and load only the signed-in user's profile/home/resume data.

**Independent Test**: Using two synthetic Google identities, authenticate both, verify each sees only its own home/workspace data, verify foreign and nonexistent IDs return the same 404 shape, and verify expired/revoked sessions return 401.

### Tests for User Story 1

- [X] T020 [P] [US1] Write auth endpoint/OpenAPI tests for start, callback, refresh, logout, cookies, and no-store headers in `backend/tests/api/test_auth_api.py`
- [X] T021 [P] [US1] Write OIDC state, nonce, PKCE, redirect, issuer, audience, expiry, signature, and return-path rejection tests in `backend/tests/services/test_google_oidc.py`
- [X] T022 [P] [US1] Write refresh hashing, rotation, replay-family revocation, expiry, logout, and deleting-account tests in `backend/tests/services/test_auth_sessions.py`
- [X] T023 [P] [US1] Write PostgreSQL identity reuse, no-email-merge, session lineage, and RLS isolation tests in `backend/tests/integration/db/test_google_identity_sessions.py`
- [X] T024 [P] [US1] Write explicit-origin credentialed CORS and Origin/Referer rejection tests in `backend/tests/api/test_browser_cors.py`
- [X] T025 [P] [US1] Write frontend login bootstrap, single-flight refresh, memory-only token, logout, and 401 tests in `frontend/tests/unit/auth-client.test.ts`
- [X] T026 [P] [US1] Write LoginGate loading/error/authenticated component tests in `frontend/tests/component/login-gate.test.tsx`
- [X] T027 [P] [US1] Write two-owner login/home/isolation/logout browser coverage in `frontend/tests/e2e/auth-workspace.spec.ts`

### Implementation for User Story 1

- [X] T028 [P] [US1] Define additive auth request/response schemas and cookie constants in `backend/app/api/schemas/auth.py`
- [X] T029 [P] [US1] Add validated Google OIDC, EPICK issuer/audience/signing, refresh pepper/TTL, callback, frontend URL, cookie, and CORS settings in `backend/app/core/config.py`
- [X] T030 [P] [US1] Implement signed OIDC transaction cookies, EPICK access-token signing/verification, and secret-safe hashing helpers in `backend/app/security/tokens.py`
- [X] T031 [US1] Extend Google subject lookup, profile update, active session lookup, family locking, and rotation persistence in `backend/app/repo/identity.py` and `backend/app/services/identity.py`
- [X] T032 [US1] Implement Google authorization URL, code exchange, discovery/JWKS validation, nonce, PKCE, and safe return-path logic in `backend/app/services/google_oidc.py`
- [X] T033 [US1] Implement refresh issuance/rotation/replay revocation, logout, access-token/session currentness, and account-status checks in `backend/app/services/auth_sessions.py`
- [X] T034 [US1] Implement `/api/v1/auth/google/start`, callback, refresh, and logout routes from `contracts/auth.openapi.yaml` in `backend/app/api/v1/auth.py`
- [X] T035 [US1] Replace the production fail-closed stub with verified EPICK bearer principal resolution while preserving test overrides in `backend/app/api/dependencies.py`
- [X] T036 [US1] Register the auth router and explicit credentialed CORS middleware with required/exposed headers in `backend/app/api/v1/router.py` and `backend/app/main.py`
- [X] T037 [P] [US1] Implement frontend auth start, refresh, logout, in-memory token state, and concurrent refresh single-flight in `frontend/lib/api/auth.ts` and `frontend/lib/state/auth-store.ts`
- [X] T038 [US1] Implement AuthProvider/LoginGate and replace the unused starter authentication path in `frontend/app/auth-provider.tsx`, `frontend/app/login-gate.tsx`, and `frontend/app/chatgpt-auth.ts`
- [X] T039 [US1] Connect current-user, home, and resume queries and render authenticated bootstrap states in `frontend/lib/queries/workspace.ts` and `frontend/app/page.tsx`

**Checkpoint**: User Story 1 passes independently against synthetic OIDC and real PostgreSQL; no token or private payload is stored in localStorage.

---

## Phase 4: User Story 2 — Experience and Application Workspace Server Persistence (Priority: P1)

**Goal**: Replace localStorage authority with W1 Activity/Episode and Project/Question APIs while preserving drafts, immutable versions, and conflict handling.

**Independent Test**: With an injected authenticated principal, create and update an experience and project, clear browser storage, reload, and recover server state; an obsolete version must produce a visible conflict instead of overwriting.

### Tests for User Story 2

- [X] T040 [P] [US2] Write Activity/Episode DTO mapping, two-step draft recovery, field-availability, and idempotency tests in `frontend/tests/unit/experience-api.test.ts`
- [X] T041 [P] [US2] Write Project/Question DTO mapping, version header, pagination, and conflict tests in `frontend/tests/unit/project-api.test.ts`
- [X] T042 [P] [US2] Write experience list/form draft/complete/resume component tests in `frontend/tests/component/experiences.test.tsx`
- [X] T043 [P] [US2] Write project/question create/edit/reload/conflict component tests in `frontend/tests/component/projects.test.tsx`
- [X] T044 [P] [US2] Extend authenticated CRUD, If-Match, idempotency, pagination, and same-404 API regression coverage in `backend/tests/api/test_frontend_workspace_contract.py`
- [X] T045 [P] [US2] Write login-stubbed experience/project persistence and browser-reload E2E in `frontend/tests/e2e/workspace-persistence.spec.ts`

### Implementation for User Story 2

- [X] T046 [P] [US2] Implement typed Activity list/detail/create/update/complete clients and query keys in `frontend/lib/api/activities.ts` and `frontend/lib/queries/activities.ts`
- [X] T047 [P] [US2] Implement typed Episode list/detail/create/update/complete clients and query keys in `frontend/lib/api/episodes.ts` and `frontend/lib/queries/episodes.ts`
- [X] T048 [US2] Implement current experience form ↔ Activity plus initial Episode mapping and resumable two-step save orchestration in `frontend/lib/api/experience-adapter.ts`
- [X] T049 [US2] Replace local experience CRUD/search counters with Activity/Episode server queries and mutations in `frontend/app/features.tsx`
- [X] T050 [P] [US2] Implement typed ApplicationProject list/detail/create/update/job-posting clients and query keys in `frontend/lib/api/projects.ts` and `frontend/lib/queries/projects.ts`
- [X] T051 [P] [US2] Implement typed Question list/detail/create/update/archive clients and query keys in `frontend/lib/api/questions.ts` and `frontend/lib/queries/questions.ts`
- [X] T052 [US2] Replace local project/question creation and editing with version-aware server mutations in `frontend/app/features.tsx`
- [X] T053 [US2] Restrict seed/localStorage behavior to an explicit development fixture provider and remove it as live authority in `frontend/app/demo-state.tsx` and `frontend/app/providers.tsx`
- [X] T054 [US2] Restore incomplete Activity/Project flows from `/home` and `/resume-items` rather than local screen state in `frontend/app/page.tsx`
- [X] T055 [US2] Implement shared 409/412 conflict reconciliation and safe user actions in `frontend/lib/api/conflicts.ts` and `frontend/components/api-conflict-dialog.tsx`

**Checkpoint**: User Story 2 works with mocked authentication and real W1/PostgreSQL even if Google login is unavailable; data survives reload without personal localStorage authority.

---

## Phase 5: User Story 3 — Asynchronous Job State and User Actions (Priority: P1)

**Goal**: Replace timer simulation with W1 Job polling and allow only versioned server-provided retry, continue-limited, stop, and cancel behavior.

**Independent Test**: Feed deterministic W1 Job fixtures through QUEUED, dispatch, RUNNING, WAITING_USER, PAUSED_RATE_LIMIT, SUCCEEDED, FAILED_RETRYABLE, CANCEL_REQUESTED, and CANCELLED; verify presentation, polling, and mutations without automatic rerun.

### Tests for User Story 3

- [X] T056 [P] [US3] Extend public Job schema/OpenAPI tests for all lifecycle/dispatch enums and private-field non-disclosure in `backend/tests/contract/test_frontend_job_contract.py`
- [X] T057 [P] [US3] Write exhaustive multi-axis Job presentation mapping and unknown-stage fallback tests in `frontend/tests/unit/job-presentation.test.ts`
- [X] T058 [P] [US3] Write visible/hidden/offline/backoff/terminal/waiting polling tests with fake timers in `frontend/tests/unit/job-polling.test.tsx`
- [X] T059 [P] [US3] Write action/retry/cancel expected-version, per-intent idempotency, and double-click tests in `frontend/tests/unit/job-actions.test.ts`
- [X] T060 [P] [US3] Write Job progress, required-action, limitation, failure, and cancel component tests in `frontend/tests/component/job-panel.test.tsx`
- [X] T061 [P] [US3] Write 202→dispatch→execution→decision→terminal and network-recovery E2E in `frontend/tests/e2e/job-lifecycle.spec.ts`

### Implementation for User Story 3

- [X] T062 [P] [US3] Implement typed Job list/detail/checkpoint/action/retry/cancel clients and query keys in `frontend/lib/api/jobs.ts` and `frontend/lib/queries/jobs.ts`
- [X] T063 [P] [US3] Implement the approved multi-axis presentation mapping with safe unknown fallbacks in `frontend/lib/jobs/presentation.ts`
- [X] T064 [US3] Implement configurable 3-second active polling, visibility/offline pause, failure backoff, and terminal/waiting stop logic in `frontend/lib/jobs/use-job-polling.ts`
- [X] T065 [US3] Implement required-action, retry, and cancel mutation hooks with expected versions and stable per-intent keys in `frontend/lib/jobs/use-job-actions.ts`
- [X] T066 [US3] Replace scenario dropdown/timer simulation with W1 Job state, progress, failure, limitation, and action UI in `frontend/app/features.tsx`
- [X] T067 [US3] Preserve active project/question/Job navigation across experience detours and reload in `frontend/app/page.tsx`
- [X] T068 [US3] Add deterministic synthetic Job fixtures without production routes in `backend/tests/fixtures/frontend_jobs.py` and `frontend/tests/mocks/job-scenarios.ts`

**Checkpoint**: User Story 3 is independently demonstrable with synthetic worker results; polling observes state only and never starts or retries work automatically.

---

## Phase 6: User Story 4 — Recommendation Comparison and Material Selection (Priority: P2)

**Goal**: Display only usable versioned recommendation candidates and persist one current material selection per question.

**Independent Test**: Use fixed SYNTHETIC runs to verify READY, LIMITED, empty, stale, and failed results; compare candidates, save/replace/clear selection, reload, and confirm stale or deleted candidates remain unavailable.

### Tests for User Story 4

- [X] T069 [P] [US4] Extend recommendation/candidate/selection OpenAPI and authorization regression tests in `backend/tests/api/test_frontend_recommendation_contract.py`
- [X] T070 [P] [US4] Write recommendation run/candidate pagination, result-origin, and error adapter tests in `frontend/tests/unit/recommendation-api.test.ts`
- [X] T071 [P] [US4] Write READY/LIMITED/stale/empty/failed candidate availability component tests in `frontend/tests/component/recommendation-candidates.test.tsx`
- [X] T072 [P] [US4] Write select/replace/clear, version warning, cache invalidation, and late-response tests in `frontend/tests/unit/material-selection.test.ts`
- [X] T073 [P] [US4] Write recommendation compare/select/reload and stale-result rejection E2E in `frontend/tests/e2e/recommendation-selection.spec.ts`

### Implementation for User Story 4

- [X] T074 [P] [US4] Implement typed recommendation run/detail/candidate clients and query keys in `frontend/lib/api/recommendations.ts` and `frontend/lib/queries/recommendations.ts`
- [X] T075 [P] [US4] Implement typed current selection/select/replace/clear clients and query keys in `frontend/lib/api/selections.ts` and `frontend/lib/queries/selections.ts`
- [X] T076 [US4] Implement candidate availability, validation, limitation, and stale-result guards in `frontend/lib/recommendations/availability.ts`
- [X] T077 [US4] Replace locally ordered candidate generation with W1 recommendation candidates and comparison data in `frontend/app/features.tsx`
- [X] T078 [US4] Connect selection save/replace/clear with targeted query invalidation and reload recovery in `frontend/app/features.tsx`
- [X] T079 [US4] Add mandatory `SYNTHETIC` versus `ENGINE` origin and limited-result labeling in `frontend/components/result-origin-badge.tsx` and `frontend/app/features.tsx`

**Checkpoint**: User Story 4 works from stored W1 synthetic results and cannot represent synthetic or stale output as a current live-engine result.

---

## Phase 7: User Story 4.5 — W4 Recommendation Execution Integration (Priority: P2)

**Goal**: Keep the W1 public recommendation API stable while an ENGINE-mode Run executes the delivered W4 recommendation code through a durable queue trigger and W1-owned protected HTTP/context/publication boundary.

**Evidence basis**: `md/w4/W1_W4_CT12_Actual_Runtime_Result_2026-09-20.md` proves the separate Question Core SQS/currentness pattern; delivered W4 `epick_w4/w1_bridge.py` implements W1's execution-port shape but remains synthetic-only and labels its publication draft. Therefore this phase reuses the proven transport controls and adds a recommendation-specific acceptance gate instead of treating CT-12 as completion.

**Independent Test**: Submit a fixed synthetic Run through the unchanged W1 public endpoint in server-selected ENGINE mode; execute the actual W4 `W1ExecutionAdapter`; verify `ENGINE` plus `LIMITED` candidates can be read/selected, while duplicate, response-loss, restart, cancel, deletion epoch, question/snapshot, and Source-currentness changes never produce duplicate, partial, stale, or synthetic-fallback publication.

### Tests for User Story 4.5

- [X] T080 [P] [US4.5] Pin W4 source/schema hashes and write private service-input, context, publication, and public-non-disclosure compatibility tests in `backend/tests/contract/test_w4_recommendation_runtime_contract.py`
- [X] T081 [P] [US4.5] Write ENGINE Run creation, immutable origin, server-selected executor, idempotency, and no-synthetic-fallback service tests in `backend/tests/services/test_engine_recommendation_runs.py`
- [X] T082 [P] [US4.5] Write acquire/load/authorize/publish/fail unit tests for owner, lease, context hash, Episode mapping, state mapping, and atomic candidate publication in `backend/tests/services/test_w4_recommendation_run_store.py`
- [X] T083 [P] [US4.5] Write PostgreSQL duplicate, concurrent lease, partial-write rollback, cancellation, owner deletion epoch, question/snapshot revision, and Source-currentness race tests in `backend/tests/integration/db/test_w4_recommendation_publication.py`
- [X] T084 [P] [US4.5] Write private outbox/SQS reference-only payload, retry/DLQ, duplicate, response-loss, and relay-restart tests in `backend/tests/runtime/test_w4_recommendation_dispatch.py`
- [X] T085 [P] [US4.5] Write workload authentication, indistinguishable not-found, context redaction, action authorization, publication validation, and safe-error tests in `backend/tests/api/test_w4_recommendation_private_api.py`
- [X] T086 [P] [US4.5] Write actual delivered W4 `W1ExecutionAdapter` integration tests with fixed synthetic context/test model clients and an HTTP-backed RunStore in `w4/EPICK_W4_HTTP_Runtime_2026-09-20_r5/tests/test_w1_http_run_store.py`
- [X] T087 [P] [US4.5] Write frontend `ENGINE` versus `SYNTHETIC`, ENGINE-plus-LIMITED, failure, and no-private-destination component tests in `frontend/tests/component/engine-recommendation.test.tsx`
- [X] T088 [US4.5] Write browser E2E for unchanged W1 recommendation endpoints backed by an actual W4 synthetic acceptance Run through candidate selection in `frontend/tests/e2e/w4-engine-recommendation.spec.ts`

### Implementation for User Story 4.5

- [X] T089 [US4.5] Adopt versioned private W4 input/context/output/publication schemas with source SHA and SHA-256 manifest in `backend/contracts/w4/v1/` without adding them to public OpenAPI
- [X] T090 [P] [US4.5] Add default-off ENGINE executor, W4 queue/private audience, pinned revision, lease, timeout, retry, and REAL-data-disabled settings in `backend/app/core/config.py` and `backend/.env.example`
- [X] T091 [US4.5] Add server-selected ENGINE Run acceptance beside the existing synthetic method while preserving the public route DTO and idempotency behavior in `backend/app/services/recommendations.py` and `backend/app/api/v1/recommendations.py`
- [X] T092 [US4.5] Add Alembic-backed execution binding, immutable Episode mapping, detailed publication, and Source dependency persistence in `backend/app/models/recommendation_execution.py` and `backend/migrations/versions/`
- [X] T093 [US4.5] Implement locked acquire/load/authorize/publish/fail repository primitives and stored-result currentness checks in `backend/app/repo/recommendation_execution.py`
- [X] T094 [US4.5] Implement the W1 RunStore service with short transactions, context hashing, approved candidate mapping, atomic publication, and fenced failure handling in `backend/app/services/w4_recommendation_run_store.py`
- [X] T095 [US4.5] Define and register workload-authenticated private acquire/context/authorize/publish/fail schemas and routes outside public OpenAPI in `backend/app/api/private/w4_recommendations.py` and `backend/app/api/private/schemas/w4_recommendations.py`
- [X] T096 [US4.5] Create the reference-only recommendation execution outbox message and extend the relay queue routing without raw input content in `backend/app/services/recommendations.py` and `backend/app/runtime/outbox_relay.py`
- [X] T097 [US4.5] Implement the W4 SQS worker and authenticated HTTP RunStore client that drive the existing `W1ExecutionAdapter` in `w4/EPICK_W4_HTTP_Runtime_2026-09-20_r5/epick_w4/w1_runtime.py` and `w4/EPICK_W4_HTTP_Runtime_2026-09-20_r5/epick_w4/w1_http_run_store.py`
- [X] T098 [US4.5] Wire bounded retry/DLQ, safe failure mapping, response-loss recovery, and explicit prohibition of ENGINE-to-SYNTHETIC fallback in `backend/app/runtime/w4_recommendation_worker.py` and the W4 runtime entrypoint
- [X] T099 [US4.5] Update result copy so `ENGINE` identifies execution origin while `LIMITED` and synthetic-input/policy/model limitations remain visible in `frontend/components/result-origin-badge.tsx` and `frontend/app/features.tsx`
- [X] T100 [US4.5] Define immutable W1-private-service and W4 recommendation-worker profiles, root-owned env-file boundaries, private networking, read-only filesystems, and pinned image inputs in `backend/infra/w4-recommendation-runtime.compose.yml` and `backend/.env.example`
- [X] T101 [P] [US4.5] Add separate recommendation Main/DLQ, encryption/redrive, W1 send-only, W4 receive/delete/change-visibility, and explicit no-purge/no-RDS policy templates in `backend/infra/w4-recommendation-execution-queue-policy.template.json` and `backend/infra/w4-recommendation-worker-policy.template.json`
- [X] T102 [US4.5] Implement a mutation-free two-sided preflight for image/source/schema digests, distinct workload identity, Queue/DLQ binding, private HTTP authentication, visibility/heartbeat bounds, REAL-disabled state, and secret-safe output in `backend/app/runtime/w4_recommendation_preflight.py` and `backend/scripts/preflight_w4_recommendation_runtime.py`
- [X] T103 [US4.5] Document the code/CI-before-AWS order, W1 migration/runtime grants, immutable image publication, Secrets Manager injection, private network, alarms, rollback, and teardown in `backend/docs/W4_RECOMMENDATION_RUNTIME_DEPLOYMENT.md`
- [ ] T104 [US4.5] Provision the isolated recommendation AWS resources only after T080–T103 pass, run and record the synthetic acceptance matrix with W1/W4 SHAs, image/schema digests, redacted identity/config evidence, queue drain, cleanup, and explicit REAL-disabled status in `specs/006-frontend-backend-integration/evidence/w4-recommendation-acceptance.md`

**Checkpoint**: An actual W4 recommendation execution can publish a fenced `ENGINE`/`LIMITED` result through W1, without changing the browser contract or enabling REAL user data; Question Core CT-12 remains separate supporting evidence.

---

## Phase 8: User Story 5 — Deletion, Logout, Company Evidence, and Safe Recovery (Priority: P2)

**Goal**: Execute scoped Activity/Project/account deletion safely, prevent late-response restoration, clear personal state on logout, and replace company mock sections with authorized W1 evidence read models.

**Independent Test**: Confirm deletion scope, start deletion, inject a late pre-deletion response, reload and log in again, and verify deleted content remains hidden while retained data remains; verify restricted company evidence and internal identifiers are never returned.

### Tests for User Story 5

- [ ] T105 [P] [US5] Write additive Activity/Project deletion preview/request/status OpenAPI tests in `backend/tests/api/test_resource_deletion_api.py`
- [ ] T106 [P] [US5] Write scoped Activity/Project deletion transitions, target planning, currentness, and public-knowledge preservation tests in `backend/tests/services/test_resource_deletion.py`
- [ ] T107 [P] [US5] Write PostgreSQL deletion RLS, Job invalidation, selection/snapshot cleanup, and late-result rejection tests in `backend/tests/integration/db/test_resource_deletion.py`
- [ ] T108 [P] [US5] Write company analysis read-model authorization, restriction, evidence redaction, and private-field exclusion tests in `backend/tests/api/test_company_analysis.py`
- [ ] T109 [P] [US5] Write frontend deletion preview/confirm/status, quarantine, and late-response generation tests in `frontend/tests/unit/resource-deletion.test.ts`
- [ ] T110 [P] [US5] Write company summary/evidence/jobs/conflicts and restriction component tests in `frontend/tests/component/company-explorer.test.tsx`
- [ ] T111 [P] [US5] Write logout/account deletion cache cancellation and stale-response tests in `frontend/tests/unit/private-cache-reset.test.ts`
- [ ] T112 [P] [US5] Write Activity/Project/account deletion and retained-data browser coverage in `frontend/tests/e2e/deletion-recovery.spec.ts`
- [ ] T113 [P] [US5] Write company analysis and restricted-evidence browser coverage in `frontend/tests/e2e/company-analysis.spec.ts`

### Implementation for User Story 5

- [ ] T114 [P] [US5] Define resource deletion preview/request/status and safe aggregate schemas in `backend/app/api/schemas/deletions.py`
- [ ] T115 [US5] Extend deletion repository queries for Activity/Project ownership, affected resources, and generic owner-visible status in `backend/app/repo/deletion.py`
- [ ] T116 [US5] Extend the existing deletion orchestration with Activity and Project scope planning, Job invalidation, and idempotent target creation in `backend/app/services/deletion.py`
- [ ] T117 [US5] Add Activity and ApplicationProject deletion preview/confirm routes with If-Match and Idempotency-Key in `backend/app/api/v1/activities.py` and `backend/app/api/v1/projects.py`
- [ ] T118 [US5] Add owner-safe generic resource deletion status routing and register it in `backend/app/api/v1/deletions.py` and `backend/app/api/v1/router.py`
- [ ] T119 [P] [US5] Define company analysis section/evidence/conflict DTOs with forbidden extras in `backend/app/api/schemas/companies.py`
- [ ] T120 [US5] Implement W1-safe company analysis projection queries and restriction redaction in `backend/app/repo/companies.py` and `backend/app/services/company_analysis.py`
- [ ] T121 [US5] Add `GET /api/v1/companies/{company_id}/analysis` with current owner authorization in `backend/app/api/v1/companies.py`
- [ ] T122 [P] [US5] Implement resource deletion and account deletion clients/query keys in `frontend/lib/api/deletions.ts` and `frontend/lib/queries/deletions.ts`
- [ ] T123 [US5] Replace immediate local Activity/Project deletion with preview/confirm/progress/quarantine UI in `frontend/app/features.tsx`
- [ ] T124 [US5] Connect account deletion preview/request/status/retry and forced logout in `frontend/app/features.tsx` and `frontend/lib/api/account.ts`
- [ ] T125 [P] [US5] Implement company catalog, job-posting, and analysis clients/query keys in `frontend/lib/api/companies.ts` and `frontend/lib/queries/companies.ts`
- [ ] T126 [US5] Replace company timers/mock evidence with W1 read models while retaining an explicit development fixture flag in `frontend/app/company-explorer.tsx`
- [ ] T127 [US5] Increment client generation, abort requests, clear private caches, and remove in-memory tokens on logout/deletion/account switch in `frontend/lib/state/auth-store.ts` and `frontend/lib/queries/query-client.ts`

**Checkpoint**: User Story 5 passes scoped deletion and evidence-boundary tests; neither browser cache nor late worker/API responses can restore invalid personal data.

---

## Phase 9: Polish and Cross-Cutting Validation

**Purpose**: Prove the complete feature, harden deployment configuration, and keep synthetic evidence distinct from live-engine evidence.

- [ ] T128 Regenerate OpenAPI and TypeScript types and record zero drift in `specs/006-frontend-backend-integration/evidence/contract-drift.md`
- [ ] T129 [P] Add bundle/source/log checks for secrets, personal localStorage, and private W2/W3/W4/Neo4j destinations in `frontend/tests/unit/security-boundary.test.ts` and `backend/tests/contract/test_auth_non_disclosure.py`
- [ ] T130 Run backend API/contract/service tests and record commands/results in `specs/006-frontend-backend-integration/evidence/backend-test-summary.md`
- [ ] T131 Run isolated PostgreSQL migration/integration/RLS tests and record results in `specs/006-frontend-backend-integration/evidence/postgresql-test-summary.md`
- [ ] T132 [P] Run Ruff check/format verification and record results in `specs/006-frontend-backend-integration/evidence/backend-quality.md`
- [ ] T133 Run frontend typecheck, lint, unit, and component suites and record results in `specs/006-frontend-backend-integration/evidence/frontend-test-summary.md`
- [ ] T134 Run the clean frontend production build and record warnings/artifact status in `specs/006-frontend-backend-integration/evidence/frontend-build.md`
- [ ] T135 Run Playwright two-account, workspace, Job, recommendation, deletion, and company flows and record results in `specs/006-frontend-backend-integration/evidence/browser-e2e.md`
- [ ] T136 [P] Add staging p95 CRUD/Job-read and polling-load smoke checks in `backend/tests/performance/test_frontend_read_models.py`
- [ ] T137 [P] Update runnable local/staging setup, OAuth registration, CORS, and troubleshooting instructions in `specs/006-frontend-backend-integration/quickstart.md` and `frontend/README.md`
- [ ] T138 Add AWS Secrets Manager/environment wiring and rollback notes for OIDC/signing/origin/W4-runtime values in `backend/docs/FRONTEND_W1_INTEGRATION_DEPLOYMENT.md`
- [ ] T139 Execute the separately approved live W2~W4/AWS release gate and record recommendation-specific `ENGINE` evidence, Question Core evidence, REAL-data approval, or explicit blockers separately in `specs/006-frontend-backend-integration/evidence/live-engine-gate.md`

**Final Checkpoint**: `quickstart.md` passes, public contracts have zero drift, all automated suites pass, browser requests target W1 only, and live-engine evidence is reported separately from synthetic evidence.

---

## Dependencies and Execution Order

### Phase Dependencies

- **Phase 1 — Setup**: Starts immediately.
- **Phase 2 — Foundational**: Depends on Phase 1 and blocks all user stories.
- **US1**: Starts after Phase 2. Required for real interactive authentication.
- **US2**: Starts after Phase 2 and is independently testable with an injected principal; full browser flow consumes US1.
- **US3**: Starts after Phase 2 and is independently testable with Job fixtures; project-screen integration consumes US2.
- **US4**: Starts after Phase 2 and is independently testable with stored synthetic runs; full flow consumes US2 and US3.
- **US4.5**: Depends on US4's frozen public recommendation/selection contract and the pinned W4 delivery. It reuses CT-12 transport patterns but requires its own recommendation publication acceptance evidence before completion.
- **US5**: Starts after Phase 2; deletion UI consumes US2. Company read-model work can proceed independently, but this plan executes US4.5 first so engine results have currentness/deletion fences before deletion integration.
- **Phase 9 — Polish**: Depends on every story selected for release. Live release gate T139 additionally depends on provisioned W2~W4/AWS infrastructure, Stage 4.5 evidence, and the applicable REAL-data approvals.

### User Story Completion Order

```text
Foundation
 ├─> US1 Authentication ───────────────┐
 ├─> US2 Workspace Persistence ──> US3 Job Control ──> US4 Recommendation ──> US4.5 W4 Runtime
 └─> US5 Company Read Model            │                    │                         │
          US2 ──> US5 Resource Deletion┴────────────────────┴─────────────────────────┘
```

- **Technical MVP**: US1 proves secure identity/session ownership.
- **Product MVP**: US1 + US2 provides login and durable personal workspace.
- **Core EPICK synthetic flow**: US1 + US2 + US3 + US4.
- **Core EPICK engine acceptance flow**: US1 + US2 + US3 + US4 + US4.5 with synthetic input and explicit limitations.
- **Release candidate**: All stories plus Phase 9; T139 can report a blocked REAL-data gate, but ENGINE activation must not be claimed without T104 and the relevant approval evidence.

### Within Each User Story

1. Write tests and verify the intended failure.
2. Add schemas/models only when the existing model is insufficient.
3. Implement repository/service logic before routes.
4. Implement API clients/query hooks before screen mutation.
5. Pass independent story tests before starting dependent integration.

## Parallel Opportunities

- Phase 1 configuration/test harness files marked `[P]` can be developed concurrently.
- Backend and frontend tests within a story can be written concurrently before implementation.
- After Phase 2, US1 backend authentication and US2 frontend DTO adapters can proceed in parallel using dependency injection and MSW.
- In US5, company read-model work is independent of resource deletion work until screen-level integration.
- In US4.5, W1 private contract/store tests and W4 HTTP RunStore/worker tests can proceed in parallel after T089 freezes hashes.
- Phase 9 static security, lint, performance, and documentation tasks can run in parallel after story code stabilizes.

## Parallel Example: User Story 1

```text
Backend test track: T020, T021, T022, T023, T024
Frontend test track: T025, T026, T027
After tests fail: backend T028–T036 and frontend T037–T039 can proceed concurrently at the API contract boundary.
```

## Parallel Example: User Story 2

```text
Activity/Episode track: T040, T042, T046–T049
Project/Question track: T041, T043, T050–T052
Shared integration after both: T053–T055 and T045
```

## Parallel Example: User Story 3

```text
Contract/fixture track: T056, T068
Polling/presentation tests: T057, T058, T060
Action tests: T059
Implementation converges at T066 and E2E T061.
```

## Parallel Example: User Story 4

```text
Recommendation track: T069, T070, T071, T074, T076
Selection track: T072, T075, T078
Screen convergence: T077, T079, then E2E T073
```

## Parallel Example: User Story 5

```text
Deletion backend track: T105–T107, T114–T118
Company backend track: T108, T119–T121
Deletion frontend track: T109, T111, T112, T122–T124, T127
Company frontend track: T110, T113, T125–T126
```

## Parallel Example: User Story 4.5

```text
Contract freeze first: T080, T089
W1 store/private API track: T081–T085, T090–T096
W4 worker/client track: T086, T097–T098
Frontend after publication works: T087–T088, T099
Deployment recipe/preflight after code and CI: T100–T103
Isolated AWS acceptance last: T104
```

## Implementation Strategy

### Technical MVP First

1. Complete Phase 1 and Phase 2.
2. Complete US1.
3. Stop and validate two-user isolation, session rotation, logout, and no credential persistence.

### Product MVP

1. Add US2 after the technical MVP.
2. Validate login → experience/project save → browser reload.
3. Deploy/demo durable personal workspace without claiming engine completion.

### Incremental Core Flow

1. Add US3 and validate Job lifecycle independently with synthetic worker fixtures.
2. Add US4 and validate recommendation/selection from stored synthetic runs.
3. Add US4.5 and validate the actual W4 recommendation runtime with fixed synthetic input; keep REAL disabled.
4. Add US5 and validate deletion/evidence boundaries.
5. Complete Phase 9, then enable an ENGINE flag only after T104; enable REAL input only after T139 records all policy/model/data gates.

## Notes

- `[P]` tasks modify different primary files or can be prepared against frozen contracts.
- Existing public schema names and enums are preserved; additive contracts require contract tests before frontend use.
- OAuth credentials and AWS resource values are deployment inputs and never test fixtures.
- Do not add a frontend call to W2, W3, W4, SQS, Neo4j, or private lookup/context adapters.
- Do not mark synthetic recommendation/company fixtures as real engine output.
- `ENGINE` denotes actual W4 execution, not REAL input or production approval; preserve the simultaneous `LIMITED`/limitation labels during Stage 4.5.
- Do not treat the Question Core CT-12 result as the W4 recommendation acceptance result, and do not silently fall back from ENGINE to SYNTHETIC.
- Commit only after the relevant checkpoint tests pass; do not stage unrelated user changes.
