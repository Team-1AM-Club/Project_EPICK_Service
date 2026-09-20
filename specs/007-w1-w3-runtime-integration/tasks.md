# Tasks: W1–W3 Actual Runtime Integration

**Input**: Design documents from `/specs/007-w1-w3-runtime-integration/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: The specification requires automated contract, persistence, authorization, failure and
joint-runtime evidence. Test tasks appear before their corresponding implementation tasks.

**Organization**: US1–US5 map directly to M1–M5. A phase whose gate is not verified must stop at its
gate task; missing inter-team values must not be replaced with placeholders that imply READY.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches a different file and has no dependency on another
  incomplete task in the same phase.
- **[Story]**: User story from `spec.md`; US1=M1, US2=M2, US3=M3, US4=M4, US5=M5.
- Every task names the file that contains its implementation or retained evidence.

## Phase 1: Setup (Shared Planning and Evidence Structure)

**Purpose**: Pin the inputs and create a non-secret evidence boundary shared by M1–M5.

- [X] T001 Record the W1 baseline SHA, W3 full SHA `c7e6788168c048941bdabe7ed8cb01007edeecec`, handoff SHA-256, adopted event version, and current gate owners in `specs/007-w1-w3-runtime-integration/evidence/source-baseline.json`
- [X] T002 [P] Document evidence redaction rules, allowed count-only fields, and forbidden secret/private fields in `specs/007-w1-w3-runtime-integration/evidence/README.md`
- [X] T003 [P] Create the initial `M1_IN_PROGRESS` milestone/gate record conforming to the readiness contract in `specs/007-w1-w3-runtime-integration/evidence/runtime-readiness.json`

---

## Phase 2: Foundational (Shared Gate and Evidence Utilities)

**Purpose**: Provide common provenance, gate enforcement, and evidence redaction before any runtime
milestone is implemented.

**⚠️ CRITICAL**: Complete this phase before US1–US5. It does not remove any W3 gate.

- [X] T004 [P] Add failing contract tests for milestone transitions, required W3-A–F gates, and forbidden false READY states in `backend/tests/contract/test_w1_w3_runtime_readiness.py`
- [X] T005 [P] Add failing tests that reject owner IDs, URLs, tokens, DB credentials, raw role identifiers, and AnalysisPlan bodies from public evidence in `backend/tests/runtime/test_w1_w3_evidence_redaction.py`
- [X] T006 [P] Add failing provenance tests for W3 full SHA and canonical handoff hash verification in `backend/tests/contract/test_w3_runtime_provenance.py`
- [X] T007 Implement readiness loading, JSON Schema validation, legal milestone transitions, and fail-closed gate assertions in `backend/app/runtime/w1_w3_runtime_readiness.py`
- [X] T008 Implement allowlist-based redaction and count-only evidence serialization in `backend/app/runtime/w1_w3_evidence.py`
- [X] T009 Implement W3 receipt/source/hash provenance verification with no network mutation in `backend/scripts/verify_w3_runtime_provenance.py`

**Checkpoint**: Provenance and gate state are machine-verifiable; only US1 is currently unblocked.

---

## Phase 3: User Story 1 — M1 Immutable Image and Single-Host State (Priority: P1) 🎯 MVP

**Goal**: Produce a locked W3 image and Compose topology that run non-root/read-only and preserve one
local `/state/core.db` across container recreation.

**Independent Test**: Build from the pinned W3 package, run network-free smoke, initialize a named
volume, recreate the container, and prove the same SQLite counters/delivery metadata remain visible.

### Tests for User Story 1

- [X] T010 [P] [US1] Add failing Dockerfile policy tests for Python 3.12, locked dependency install, non-root UID, no embedded secrets, and a read-only-compatible entrypoint in `backend/tests/contract/test_w3_runtime_image.py`
- [X] T011 [P] [US1] Add failing Compose contract tests for one local named volume at `/state`, `/state/core.db`, bounded tmpfs, read-only rootfs, dropped capabilities, and no host/NFS state path in `backend/tests/contract/test_w3_runtime_deployment.py`
- [X] T012 [P] [US1] Add failing preflight tests for image/source provenance, runtime UID, mount topology, database initialization, and restart persistence in `backend/tests/runtime/test_w3_runtime_preflight.py`
- [X] T013 [P] [US1] Add an opt-in Docker integration test that recreates the W3 container and compares count-only `inspect` state in `backend/tests/runtime/test_w3_runtime_persistence.py`

### Implementation for User Story 1

- [X] T014 [P] [US1] Add a reproducible multi-stage Python 3.12 build using `uv.lock`, a fixed non-root runtime user, and no credentials in `w3/Dockerfile`
- [X] T015 [P] [US1] Restrict the W3 build context to reviewed source and dependency metadata in `w3/.dockerignore`
- [X] T016 [US1] Define init/smoke/inspect and reusable one-shot command services sharing `epick-w3-core-runtime-state:/state` in `backend/infra/w3-runtime.compose.yml`
- [X] T017 [US1] Implement source/digest/UID/rootfs/mount/SQLite/restart checks without live SQS sends in `backend/app/runtime/w3_runtime_preflight.py`
- [X] T018 [US1] Expose the M1 preflight as an operator command with secret-safe JSON output in `backend/scripts/preflight_w3_actual_runtime.py`
- [X] T019 [P] [US1] Add least-privilege ECR build/push and Worker pull policy placeholders without account-specific values in `backend/infra/w3-runtime-ecr-policy.template.json`
- [ ] T020 [US1] Build, smoke, recreate, publish by commit tag, resolve the ECR manifest digest, and save redacted M1 evidence in `specs/007-w1-w3-runtime-integration/evidence/m1-image-state.json` (local build/smoke/recreate/evidence complete; ECR publish and manifest digest wait for W3-D)
- [X] T021 [US1] Prepare the W3-D review request with Dockerfile/Compose paths, source SHA, image digest, non-root/read-only proof, and restart evidence in `md/deploy/W1_W3_M1_Image_Review_Request_2026-09-20.md`; keep M1 open until W3-D is recorded

**Checkpoint**: M1 is locally/deployment verified; final M1 closure requires W3-D review. Do not start
M4 merely because an image exists.

---

## Phase 4: User Story 2 — M2 Actual Supplier and Authority (Priority: P1)

**Goal**: Bind the named actual AnalysisPlan caller to a minimal W1 currentness service and a W3
Authority adapter that fails closed at supply, relay, and replay.

**Independent Test**: Current context succeeds; source/input/owner/epoch mismatch, cancel/delete,
401/403/404/409/503, and timeout produce no W3 send or new W1 decision.

**Gate**: W3-A and W3-B must both be `VERIFIED` before T023. No synthetic caller/adapter may satisfy this gate.

### Gate and Tests for User Story 2

- [ ] T022 [US2] Validate W3-A caller repository/full SHA/file/function/semantics/idempotency and W3-B adapter owner/timeout/error mapping, record evidence in `specs/007-w1-w3-runtime-integration/evidence/m2-gate.json`, and stop Phase 4 if either gate is not verified
- [ ] T023 [P] [US2] Add failing JSON Schema and compatibility tests for the candidate Authority request/response against W3 `DecisionContext` and `Authorization` in `backend/tests/contract/test_w3_authority_contract.py`
- [ ] T024 [P] [US2] Add failing PostgreSQL integration tests for current context, ownership, source/company/input binding, cancellation, deletion epoch, and column-minimal lookup access in `backend/tests/integration/db/test_w3_authority_currentness.py`
- [ ] T025 [P] [US2] Add failing private HTTP tests for bearer/principal checks, 401/403/404/409/503, timeout-safe error bodies, and absence of private content in `backend/tests/runtime/test_w3_authority_adapter.py`

### Implementation for User Story 2

- [ ] T026 [P] [US2] Publish the adopted request and response schemas under W1's runtime contract namespace in `backend/contracts/w1/v1/w3-authority.request.schema.json` and `backend/contracts/w1/v1/w3-authority.response.schema.json`
- [ ] T027 [US2] Implement owner-derived, column-minimal User→Job→Source currentness projections in `backend/app/repo/w3_authority.py`
- [ ] T028 [US2] Implement current context, active/cancel/delete/epoch/source checks and retryable versus terminal outcomes in `backend/app/services/w3_authority.py`
- [ ] T029 [US2] Implement the isolated private FastAPI Authority application and safe error mapping in `backend/app/runtime/w3_authority_adapter.py`
- [ ] T030 [P] [US2] Add W3 Authority URL, principal, bearer, timeout, and lookup DB settings with startup validation in `backend/app/core/config.py`
- [ ] T031 [US2] Add the Authority process entrypoint and loopback/private-network-only Compose profile in `backend/scripts/run_w3_authority_adapter.py` and `backend/infra/w1-runtime.compose.yml`
- [ ] T032 [US2] Add only the required User/Job/Source-link column grants and assert forbidden content-column access in `backend/infra/postgres/runtime_privileges.sql` and `backend/scripts/postgres_runtime_privilege_preflight.py`
- [ ] T033 [US2] Verify the W3-B adapter revision uses the adopted contract and rechecks supply/relay/replay, then record its full SHA and test result in `specs/007-w1-w3-runtime-integration/evidence/m2-w3-adapter.json`
- [ ] T034 [US2] Run the M2 contract, PostgreSQL, HTTP, and W3 adapter failure matrix and record zero side effects for rejected cases in `specs/007-w1-w3-runtime-integration/evidence/m2-authority-result.json`

**Checkpoint**: Actual caller and currentness adapter work together without trusting caller-supplied owner/current flags.

---

## Phase 5: User Story 3 — M3 Deletion and Runtime Operations (Priority: P1)

**Goal**: Make W3 an explicit durable deletion target and operate relay/expire/inspect/HELD under an
approved retention policy without raw-state restoration or automatic replay.

**Independent Test**: Duplicate/restarted deletion delivery converges; deletion-first sends nothing;
send-first is rejected by W1 currentness; another owner and shared counters remain unchanged; exhausted
delivery enters HELD and alerts without replay.

**Gate**: W3-C, W3-E, and a revisioned product/privacy retention approval must be `VERIFIED` before T036.

### Gate and Tests for User Story 3

- [ ] T035 [US3] Validate W3-C delete-owner outcome/ack/retry mapping, W3-E state semantics, and retention/tombstone approval revision in `specs/007-w1-w3-runtime-integration/evidence/m3-gate.json`, and stop Phase 5 if any input is missing
- [ ] T036 [P] [US3] Add failing migration tests for the additive `W3_CORE_RUNTIME` deletion target constraint and downgrade prohibition in `backend/tests/integration/db/test_w3_deletion_target_migration.py`
- [ ] T037 [P] [US3] Add failing transaction tests proving W1 deletion stages exactly one W3 target/outbox command with the current owner epoch in `backend/tests/integration/db/test_w3_deletion_dispatch.py`
- [ ] T038 [P] [US3] Add failing worker tests for duplicate delivery, stale epoch, W3 unavailable, restart, idempotent ACK, and retryable failure mapping in `backend/tests/runtime/test_w3_deletion_worker.py`
- [ ] T039 [P] [US3] Add failing lifecycle tests for relay/expire/inspect, HELD alert/no-auto-replay, approved retention, and raw-restore rejection in `backend/tests/runtime/test_w3_runtime_operations.py`
- [ ] T040 [P] [US3] Add failing PostgreSQL/W3 race tests for deletion-first, send-first, late SQS delivery, and another-owner/shared-Source preservation in `backend/tests/integration/db/test_w3_deletion_races.py`

### Implementation for User Story 3

- [ ] T041 [US3] Add the forward-only `W3_CORE_RUNTIME` deletion target constraint and required runtime grants in `backend/migrations/versions/031_w3_core_runtime_deletion_target.py`
- [ ] T042 [US3] Update the ORM deletion target constraint and typed store set in `backend/app/models/deletion.py`
- [ ] T043 [US3] Stage one W3 deletion target and versioned command inside the existing confirmed-deletion transaction in `backend/app/services/deletion.py`
- [ ] T044 [US3] Implement the approved W3-C transport, idempotent delete-owner result mapping, and W1 acknowledge/failure transitions in `backend/app/runtime/w3_deletion_worker.py`
- [ ] T045 [US3] Add the bounded W3 deletion worker entrypoint with secret-safe structured output in `backend/scripts/run_w3_deletion_worker.py`
- [ ] T046 [US3] Add relay-once, expire, inspect, and deletion worker profiles sharing the single W3 state volume in `backend/infra/w3-runtime.compose.yml`
- [ ] T047 [P] [US3] Add bounded systemd service/timer templates for relay, expire, and HELD inspection in `backend/infra/systemd/epick-w3-core-runtime.service` and `backend/infra/systemd/epick-w3-core-runtime.timer`
- [ ] T048 [US3] Implement HELD count detection, alert output, and an explicit prohibition on automatic replay in `backend/scripts/inspect_w3_core_runtime.py`
- [ ] T049 [P] [US3] Document approved retention, schedules, HELD ownership, manual replay authorization, quarantine backup, and unsupported raw restore in `md/deploy/W1_W3_Runtime_Operations_Runbook_2026-09-20.md`
- [ ] T050 [US3] Run deletion duplicate/restart/race and operations tests and store count-only M3 results in `specs/007-w1-w3-runtime-integration/evidence/m3-deletion-operations.json`

**Checkpoint**: W3 private state participates in W1's durable deletion workflow and approved operations; no production READY claim precedes all M3 evidence.

---

## Phase 6: User Story 4 — M4 Actual IAM and SQS Binding (Priority: P1)

**Goal**: Replace the T043 synthetic sender identity with an actual W3 send-only workload role while
retaining W1's receive-only consumer boundary.

**Independent Test**: W3 stable STS Role ID equals W3 expected ID and W1 expected SenderId; W3 can
send only to the Main Queue and cannot receive/delete/purge or access W1 DB/secrets.

**Gate**: M1 and M2 must be complete and the actual W3 compute identity must be assigned before T052.

### Gate and Tests for User Story 4

- [ ] T051 [US4] Assert M1/M2 completion and actual compute identity assignment in `specs/007-w1-w3-runtime-integration/evidence/m4-gate.json`, and stop Phase 6 when the workload principal is not exact
- [ ] T052 [P] [US4] Add failing policy-template tests for exact Main Queue SendMessage, W1 receive/delete/change-visibility, DLQ inspection, and explicit absence of W3 receive/delete/purge/DB/secret permissions in `backend/tests/contract/test_w3_actual_runtime_policies.py`
- [ ] T053 [P] [US4] Add failing preflight tests for assumed-role ARN shape, stable Role ID equality, Main/DLQ redrive binding, and secret-safe errors in `backend/tests/runtime/test_w3_actual_runtime_preflight.py`

### Implementation for User Story 4

- [ ] T054 [P] [US4] Add the account-agnostic actual W3 Main Queue send-only IAM policy template in `backend/infra/w3-core-decision-sender-policy.template.json`
- [ ] T055 [P] [US4] Add the Main Queue resource policy template restricted to the exact actual W3 role principal in `backend/infra/w3-core-decision-queue-policy.template.json`
- [ ] T056 [US4] Extend the actual runtime preflight to compare W3 STS Role ID, configured W3 expected ID, W1 expected SenderId, queue attributes, and negative capability probes in `backend/app/runtime/w3_runtime_preflight.py`
- [ ] T057 [US4] Create/attach the actual role and queue policies through the approved AWS operator path and save redacted ARN/Role-ID fingerprints and policy hashes in `specs/007-w1-w3-runtime-integration/evidence/m4-iam-binding.json`
- [ ] T058 [US4] Run both W3 send-side and W1 receive-side preflights plus negative receive/delete/purge/DB/secret probes and record results in `specs/007-w1-w3-runtime-integration/evidence/m4-preflight-result.json`

**Checkpoint**: M1–M4 are READY for joint CT-12; this is not yet service-integration completion.

---

## Phase 7: User Story 5 — M5 Actual-Producer Joint CT-12 (Priority: P1)

**Goal**: Prove actual W3 caller/supplier/outbox/relay → AWS SQS → W1 consumer/PostgreSQL behavior,
including duplicates, restarts, failures, cancellation/deletion, explicit retry, stale fencing, and cleanup.

**Independent Test**: CT12-01~12 all pass with reconciled W3/SQS/W1 counts, zero automatic commands
before retry, exactly one new-fence command after retry, zero stale late effects, and recorded teardown.

**Gate**: M1–M4 and W3-F must be complete before T060.

### Gate and Tests for User Story 5

- [ ] T059 [US5] Verify M1–M4 evidence, W3-F executor/window, both full SHAs/image digests, and the actual role fingerprint in `specs/007-w1-w3-runtime-integration/evidence/m5-gate.json`, and stop Phase 7 if any pin differs
- [ ] T060 [P] [US5] Add failing contract tests that require all CT12-01~12 scenario IDs, expected count fields, revision pins, and cleanup fields in `backend/tests/contract/test_w1_w3_ct12_evidence.py`
- [ ] T061 [P] [US5] Add failing harness tests for scenario isolation, failpoint reset, count reconciliation, no secret leakage, and refusal to run against non-isolated resources in `backend/tests/runtime/test_w1_w3_ct12_harness.py`

### Implementation and Joint Execution for User Story 5

- [ ] T062 [US5] Implement guarded CT-12 environment validation, scenario orchestration, failpoint control, and W3/SQS/W1 count collection in `backend/app/runtime/w1_w3_ct12_harness.py`
- [ ] T063 [US5] Add the opt-in actual joint runner requiring an explicit execution flag and immutable pins in `backend/scripts/run_w1_w3_ct12_joint.py`
- [ ] T064 [P] [US5] Add the secret-redacted joint evidence serializer and JSON Schema validation in `backend/app/runtime/w1_w3_ct12_evidence.py`
- [ ] T065 [US5] Provision the isolated PostgreSQL database, Main Queue/DLQ window, root-only env files, actual W3 state volume, and initial zero-count snapshot in `specs/007-w1-w3-runtime-integration/evidence/m5-environment.json`
- [ ] T066 [US5] Execute CT12-01~04 for actual Core, duplicate, mutated same-ID, and Non-Core paths and append W3/SQS/W1 counts to `specs/007-w1-w3-runtime-integration/evidence/m5-scenarios-01-04.json`
- [ ] T067 [US5] Execute CT12-05~07 for wrong principal, W3 restart/state persistence, SQS/W1 DB failure, and ACK-loss redelivery in `specs/007-w1-w3-runtime-integration/evidence/m5-scenarios-05-07.json`
- [ ] T068 [US5] Execute CT12-08~10 for cancellation, owner deletion race/other-owner preservation, and HELD/no-auto-replay in `specs/007-w1-w3-runtime-integration/evidence/m5-scenarios-08-10.json`
- [ ] T069 [US5] Execute CT12-11~12 for explicit retry/new fence, stale late result rejection, and cleanup readiness in `specs/007-w1-w3-runtime-integration/evidence/m5-scenarios-11-12.json`
- [ ] T070 [US5] Reconcile W3 inspect, SQS visible/inflight/delayed/DLQ, and W1 receipt/decision/binding/action/command/outbox counts in `specs/007-w1-w3-runtime-integration/evidence/m5-count-reconciliation.json`
- [ ] T071 [US5] Stop actual W3/W1 test processes, remove disposable DB/queues/state/env or record approved retention, and save the result in `specs/007-w1-w3-runtime-integration/evidence/m5-teardown.json`
- [ ] T072 [US5] Mark `JOINT_CT12_COMPLETE` only after all evidence validates and publish the W1→W3 result in `md/deploy/W1_W3_Joint_CT12_Result_2026-09-20.md` and `specs/007-w1-w3-runtime-integration/evidence/runtime-readiness.json`

**Checkpoint**: W1↔W3 actual service integration is complete. M1–M4 alone never satisfy this checkpoint.

---

## Phase 8: Polish & Cross-Cutting Validation

**Purpose**: Regress existing boundaries, audit secret handling, and synchronize handoff/checklist state.

- [ ] T073 [P] Run W3 locked smoke/core tests and W1 contract/runtime/PostgreSQL suites, recording commands and totals in `specs/007-w1-w3-runtime-integration/evidence/regression-summary.json`
- [ ] T074 [P] Scan tracked changes and generated evidence for credentials, Queue URLs, owner IDs, AnalysisPlan bodies, DB URLs, and raw role identifiers, recording only pass/fail and scanner revision in `specs/007-w1-w3-runtime-integration/evidence/security-audit.json`
- [ ] T075 Verify every command and expected result in `specs/007-w1-w3-runtime-integration/quickstart.md` against the final implementation and correct any drift in that file
- [ ] T076 [P] Update W1 responsibility/completion status and remaining external gates in `md/checklist/W1_IMPLEMENTATION_AND_RESPONSIBILITY_CHECKLIST.md`
- [ ] T077 Re-run readiness schema validation and confirm the final status does not claim W2, W4, or Neo4j completion in `specs/007-w1-w3-runtime-integration/evidence/runtime-readiness.json`

---

## Dependencies & Execution Order

### Phase Dependencies

```text
Phase 1 Setup
  └─ Phase 2 Foundation
       ├─ US1 / M1 ───────────────┐
       ├─ US2 / M2 [W3-A/B] ─────┼─> US4 / M4 ──┐
       └─ US3 / M3 [W3-C/E+승인] ┘              ├─> US5 / M5 [W3-F]
                                                 └─> Phase 8 Polish
```

- **Phase 1 → Phase 2**: Sequential; all stories rely on provenance, gate, and redaction utilities.
- **US1/M1**: Starts immediately after Phase 2; W3-D is required only for final M1 closure.
- **US2/M2**: Starts only after W3-A/B; it does not require US1 to begin.
- **US3/M3**: Starts only after W3-C/E and retention approval; it can be implemented independently
  of US2 once its own gate is closed.
- **US4/M4**: Depends on completed US1 and US2 because both the deployed workload and actual
  currentness identity must be known.
- **US5/M5**: Depends on completed US1–US4 and W3-F.
- **Phase 8**: Runs after the milestones selected for delivery; final completion requires all stories.

### Within Each User Story

- The gate task runs first and stops the phase if required inputs are absent.
- Tests are written and shown failing before implementation.
- Contract/model/migration work precedes services and runtime adapters.
- Preflight precedes AWS mutation or joint execution.
- Evidence validation precedes any milestone state transition to COMPLETE.

## Parallel Opportunities

- Phase 1 T002 and T003 can run in parallel after T001 establishes naming/pins.
- Phase 2 test tasks T004–T006 can run in parallel; T007–T009 then implement separate utilities.
- US1 tests T010–T013 and implementation files T014/T015/T019 are independent.
- After W3-A/B, US2 contract, DB, and HTTP tests T023–T025 can run in parallel.
- After W3-C/E, US3 migration, worker, lifecycle, and race tests T036–T040 can run in parallel.
- US4 policy and preflight tests T052/T053 and policy templates T054/T055 are parallelizable.
- US5 evidence contract and harness tests T060/T061 are parallel; scenario execution remains
  sequential because all scenarios share one pinned environment and require deterministic reset.
- Final regression, security audit, and checklist update T073/T074/T076 can run in parallel.

## Parallel Examples

### User Story 1

```text
Task T010: Dockerfile policy tests in backend/tests/contract/test_w3_runtime_image.py
Task T011: Compose contract tests in backend/tests/contract/test_w3_runtime_deployment.py
Task T012: Preflight tests in backend/tests/runtime/test_w3_runtime_preflight.py
Task T013: Persistence test in backend/tests/runtime/test_w3_runtime_persistence.py
```

### User Story 2 after W3-A/B

```text
Task T023: Authority JSON contract tests
Task T024: PostgreSQL currentness tests
Task T025: Private HTTP authentication/error tests
```

### User Story 3 after W3-C/E and retention approval

```text
Task T036: Migration test
Task T037: Deletion transaction test
Task T038: Deletion worker test
Task T039: Operations lifecycle test
Task T040: Cross-store race test
```

## Implementation Strategy

### MVP First — M1 Only

1. Complete Phase 1 and Phase 2.
2. Complete US1 through local image/state evidence.
3. Send T021 to W3 for W3-D review.
4. Stop and validate M1; do not guess W3-A/B/C/E/F while waiting.
5. Close M1 only after W3-D disposition is recorded.

### Incremental Delivery

1. **M1**: immutable deployable W3 runtime and durable single-host state.
2. **M2**: actual caller and Authority currentness after W3-A/B.
3. **M3**: deletion and operations after W3-C/E and retention approval.
4. **M4**: actual IAM/SQS binding after M1/M2.
5. **M5**: actual-producer joint CT-12 and teardown after all gates.

Each milestone retains independent evidence and can stop safely. Only M5 changes the integration
status to `JOINT_CT12_COMPLETE`.

## Notes

- Tasks T022, T035, T051, and T059 are hard stops, not paperwork-only checks.
- W3 domain code under `w3/src/w3_knowledge/` is not modified under the pinned SHA. Required W3
  changes must arrive as a new W3-owned full SHA and pass provenance review.
- Actual AWS identifiers and secrets never enter these task descriptions or evidence files.
- `TRANSPORT_HANDOFF` proves SQS acceptance, not W1 application.
- Commit after each validated phase or coherent milestone; do not commit generated secrets/runtime DBs.
