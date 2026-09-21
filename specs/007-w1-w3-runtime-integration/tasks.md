# Tasks: W1–W3 Actual Runtime Integration

**Input**: Design documents from `/specs/007-w1-w3-runtime-integration/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: The specification requires automated contract, persistence, authorization, failure and
joint-runtime evidence. Test tasks appear before their corresponding implementation tasks.

**Organization**: US1–US5 map directly to M1–M5. W1-owned contract, test and adapter work may run
before an external gate, but each phase must stop before actual cutover or completion when its gate
is not verified; missing inter-team values must not be replaced with placeholders that imply READY.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches a different file and has no dependency on another
  incomplete task in the same phase.
- **[Story]**: User story from `spec.md`; US1=M1, US2=M2, US3=M3, US4=M4, US5=M5.
- Every task names the file that contains its implementation or retained evidence.

## Phase 1: Setup (Shared Planning and Evidence Structure)

**Purpose**: Pin the inputs and create a non-secret evidence boundary shared by M1–M5.

- [X] T001 Re-baseline the W1 SHA, W3 implementation SHA `66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b`, receipt HEAD `bad8671b2ea8d02bdce2157120b94d2b7edf09d8`, clone path, adopted event/policy versions, focused-test result, superseded M1 digest, and current gate owners in `specs/007-w1-w3-runtime-integration/evidence/source-baseline.json`
- [X] T002 [P] Document evidence redaction rules, allowed count-only fields, and forbidden secret/private fields in `specs/007-w1-w3-runtime-integration/evidence/README.md`
- [X] T003 [P] Update the readiness record within the existing schema to `M1_IN_PROGRESS`, W3-C/E `VERIFIED`, and W3-A/B/D/F `OPEN`, marking stale M1 evidence without claiming runtime deployment, in `specs/007-w1-w3-runtime-integration/evidence/runtime-readiness.json`

---

## Phase 2: Foundational (Shared Gate and Evidence Utilities)

**Purpose**: Provide common provenance, gate enforcement, and evidence redaction before any runtime
milestone is implemented.

**⚠️ CRITICAL**: Complete this phase before US1–US5. It does not remove any W3 gate.

- [X] T004 [P] Update contract tests for received/verified W3 gates, W1-work-versus-cutover transitions, implementation/receipt dual pins, and forbidden false READY states in `backend/tests/contract/test_w1_w3_runtime_readiness.py`
- [X] T005 [P] Add failing tests that reject owner IDs, URLs, tokens, DB credentials, raw role identifiers, and AnalysisPlan bodies from public evidence in `backend/tests/runtime/test_w1_w3_evidence_redaction.py`
- [X] T006 [P] Replace handoff-receipt provenance tests with independent-clone tests for remote, implementation object, receipt ancestry, clean tree, zero runtime-source drift, readiness pin, and archive allowlist in `backend/tests/contract/test_w3_runtime_provenance.py`
- [X] T007 Update readiness loading, JSON Schema validation, legal W1-work/cutover transitions, and fail-closed gate assertions in `backend/app/runtime/w1_w3_runtime_readiness.py`
- [X] T008 Implement allowlist-based redaction and count-only evidence serialization in `backend/app/runtime/w1_w3_evidence.py`
- [X] T009 Implement network-free Git provenance verification for `w3/Project_EPICK_Service`, including implementation/receipt SHAs and runtime-path drift checks, in `backend/scripts/verify_w3_runtime_provenance.py`

**Checkpoint**: Provenance and gate state are machine-verifiable; W1-owned US1/US2/US3 work is
unblocked while actual cutover/completion gates remain fail-closed.

---

## Phase 3: User Story 1 — M1 Immutable Image and Single-Host State (Priority: P1) 🎯 MVP

**Goal**: Produce a locked W3 image and Compose topology that run non-root/read-only and preserve one
local `/state/core.db` across container recreation.

**Independent Test**: Export and build the exact pinned W3 implementation commit, run network-free smoke, initialize a named
volume, recreate the container, and prove the same SQLite counters/delivery metadata remain visible.

### Tests for User Story 1

- [X] T010 [P] [US1] Update Dockerfile policy tests for a W1-owned recipe, exact `66a0e3e1...` archive input, Python 3.12, locked dependencies, non-root UID, no embedded secrets or `.git/.venv`, and a read-only-compatible entrypoint in `backend/tests/contract/test_w3_runtime_image.py`
- [X] T011 [P] [US1] Update Compose contract tests for one local named volume at `/state`, `/state/core.db`, `w3.retention/1.1` handoff value `1209600`, bounded tmpfs, read-only rootfs, dropped capabilities, and no host/NFS state path in `backend/tests/contract/test_w3_runtime_deployment.py`
- [X] T012 [P] [US1] Update preflight tests for implementation/receipt provenance, image label/digest, runtime UID, mount topology, policy revision, database migration blockers, and restart persistence in `backend/tests/runtime/test_w3_runtime_preflight.py`
- [X] T013 [P] [US1] Update the opt-in Docker integration test to recreate the `3b23...` W3 container and compare its safe `inspect` policy/migration-blocker/delivery state; counter/tombstone persistence remains covered by the pinned W3 integration tests because `inspect_report` does not expose those counts, in `backend/tests/runtime/test_w3_runtime_persistence.py`

### Implementation for User Story 1

- [X] T014 [P] [US1] Add the reproducible W1-owned multi-stage Python 3.12 recipe using the pinned archive, `uv.lock`, a fixed non-root runtime user, OCI implementation/receipt labels, and no credentials in `backend/infra/w3-runtime.Dockerfile`
- [X] T015 [P] [US1] Implement an allowlisted, clean temporary build-context export from W3 commit `66a0e3e1...` without `.git`, `.venv`, working-tree files, or secrets in `backend/scripts/export_w3_runtime_context.py`
- [X] T016 [US1] Update init/smoke/inspect/relay/expire/backup one-shot services to use `1209600` seconds and share only `epick-w3-core-runtime-state:/state` in `backend/infra/w3-runtime.compose.yml`
- [X] T017 [US1] Update source/digest/UID/rootfs/mount/SQLite/policy/migration-blocker/restart checks without live SQS sends in `backend/app/runtime/w3_runtime_preflight.py`
- [X] T018 [US1] Update the M1 operator command to remove the obsolete `3600` retention input and emit secret-safe dual-pin/policy evidence in `backend/scripts/preflight_w3_actual_runtime.py`
- [X] T019 [P] [US1] Add least-privilege ECR build/push and Worker pull policy placeholders without account-specific values in `backend/infra/w3-runtime-ecr-policy.template.json`
- [ ] T020 [US1] Build from the `66a0e3e1...` archive, rerun the focused W3 tests, smoke/recreate the volume, publish by implementation tag, resolve the ECR manifest digest, and replace stale prior-image evidence in `specs/007-w1-w3-runtime-integration/evidence/m1-image-state.json`
- [X] T021 [US1] Refresh the W3-D review request with dual SHAs, W1-owned Dockerfile/export paths, policy revision, new image digest, non-root/read-only proof, migration-blocker result, and restart evidence in `md/deploy/W1_W3_M1_Image_Review_Request_2026-09-20.md`; keep M1 open until W3-D is recorded

**Checkpoint**: M1 is locally/deployment verified; final M1 closure requires W3-D review. Do not start
M4 merely because an image exists.

---

## Phase 4: User Story 2 — M2 Actual Supplier and Authority (Priority: P1)

**Goal**: Bind the named actual AnalysisPlan caller to a minimal W1 currentness service and a W3
Authority adapter that fails closed at supply, relay, and replay.

**Independent Test**: Current context succeeds; source/input/owner/epoch mismatch, cancel/delete,
401/403/404/409/503, and timeout produce no W3 send or new W1 decision.

**Gate**: T023–T032 are W1-owned and may proceed now. W3-A and W3-B must both be `VERIFIED` before
T033 actual caller/client cutover; no synthetic caller/adapter may satisfy that gate.

### Gate and Tests for User Story 2

- [X] T022 [US2] Record W3-A/B as open with the exact missing caller repository/SHA/function/trusted issued-at/required-vs-optional/idempotency and client auth/timeout/error mapping in `specs/007-w1-w3-runtime-integration/evidence/m2-gate.json`; permit T023–T032 but fail closed before T033 while either gate is open
- [X] T023 [P] [US2] Add failing JSON Schema and compatibility tests for the candidate Authority request/response against W3 `DecisionContext` and `Authorization` in `backend/tests/contract/test_w3_authority_contract.py`
- [X] T024 [P] [US2] Add failing PostgreSQL integration tests for current context, ownership, source/company/input binding, cancellation, deletion epoch, and column-minimal lookup access in `backend/tests/integration/db/test_w3_authority_currentness.py`
- [X] T025 [P] [US2] Add failing private HTTP tests for bearer/principal checks, 401/403/404/409/503, timeout-safe error bodies, and absence of private content in `backend/tests/runtime/test_w3_authority_adapter.py`

### Implementation for User Story 2

- [X] T026 [P] [US2] Publish the adopted request and response schemas under W1's runtime contract namespace in `backend/contracts/w1/v1/w3-authority.request.schema.json` and `backend/contracts/w1/v1/w3-authority.response.schema.json`
- [X] T027 [US2] Implement owner-derived, column-minimal User→Job→Source currentness projections in `backend/app/repo/w3_authority.py`
- [X] T028 [US2] Implement current context, active/cancel/delete/epoch/source checks and retryable versus terminal outcomes in `backend/app/services/w3_authority.py`
- [X] T029 [US2] Implement the isolated private FastAPI Authority application and safe error mapping in `backend/app/runtime/w3_authority_adapter.py`
- [X] T030 [P] [US2] Add W3 Authority URL, principal, bearer, timeout, and lookup DB settings with startup validation in `backend/app/core/config.py`
- [X] T031 [US2] Add the Authority process entrypoint and loopback/private-network-only Compose profile in `backend/scripts/run_w3_authority_adapter.py` and `backend/infra/w1-runtime.compose.yml`
- [X] T032 [US2] Add only the required User/Job/Source-link column grants and assert forbidden content-column access in `backend/infra/postgres/runtime_privileges.sql` and `backend/scripts/postgres_runtime_privilege_preflight.py`
- [ ] T033 [US2] After W3-A/B verification, bind the named actual caller and pinned W3 Authority client to the adopted contract, prove supply/relay/replay currentness rechecks, and record caller/adapter full SHAs and tests in `specs/007-w1-w3-runtime-integration/evidence/m2-w3-adapter.json`
- [ ] T034 [US2] Run the M2 contract, PostgreSQL, HTTP, and W3 adapter failure matrix and record zero side effects for rejected cases in `specs/007-w1-w3-runtime-integration/evidence/m2-authority-result.json`

**Checkpoint**: Actual caller and currentness adapter work together without trusting caller-supplied owner/current flags.

---

## Phase 5: User Story 3 — M3 Deletion, Source Retirement, and Runtime Operations (Priority: P1)

**Goal**: Make W3 an explicit durable owner-deletion and permanent Source-retirement target and
operate relay/expire/inspect/backup/HELD under `w3.retention/1.1` without raw-state restoration or
automatic replay.

**Independent Test**: Duplicate/restarted deletion delivery converges; deletion-first sends nothing;
send-first is rejected by W1 currentness; only permanent Source retirement invokes `retire_source`;
another owner and unrelated counters remain unchanged; exhausted delivery enters HELD and alerts
without replay.

**Gate**: `w3.retention/1.1` and W3-C private transport/authentication/receipt semantics are adopted,
and W3 implementation `66a0e3e1...` supplies the command consumer plus transactional receipt outbox.
T046A and T050 are complete; actual private queue binding and M3 duplicate/restart E2E were verified
on AWS staging on 2026-09-21.

### Gate and Tests for User Story 3

- [X] T035 [US3] Record pinned `delete_owner`/`retire_source`/expire/backup semantics, `w3.retention/1.1`, the adopted private dispatcher transport/authentication/ACK/retry decision, and the verified W3 implementation revision in `specs/007-w1-w3-runtime-integration/evidence/m3-gate.json`
- [X] T036 [P] [US3] Add failing migration tests for the additive `W3_CORE_RUNTIME` deletion target constraint/downgrade prohibition and contract tests for permanent Source-retirement commands in `backend/tests/integration/db/test_w3_deletion_target_migration.py` and `backend/tests/contract/test_w3_source_retirement_contract.py`
- [X] T037 [P] [US3] Add failing transaction tests proving W1 deletion stages exactly one W3 target/outbox command with the current owner epoch in `backend/tests/integration/db/test_w3_deletion_dispatch.py`
- [X] T038 [P] [US3] Add failing worker tests for duplicate owner deletion and Source retirement, stale epoch, transient unavailable/unknown Source, W3 unavailable, restart, idempotent ACK, and retryable failure mapping in `backend/tests/runtime/test_w3_deletion_worker.py` and `backend/tests/runtime/test_w3_source_retirement_worker.py`
- [X] T039 [P] [US3] Add failing lifecycle tests for relay/expire/inspect/redacted quarantine backup, HELD alert/no-auto-replay, exact `w3.retention/1.1` deadlines, migration blockers, and raw-restore rejection in `backend/tests/runtime/test_w3_runtime_operations.py`
- [X] T040 [P] [US3] Add failing PostgreSQL/W3 race tests for deletion-first, send-first, late SQS delivery, permanent retirement versus transient unavailable/unknown, and another-owner/unrelated-Source preservation in `backend/tests/integration/db/test_w3_deletion_races.py`

### Implementation for User Story 3

- [X] T041 [US3] Add the forward-only `W3_CORE_RUNTIME` deletion target constraint, required runtime grants, and publish the Source-retirement schema later adopted exactly from W3 in `backend/migrations/versions/035_w3_core_runtime_deletion_target.py` and `backend/contracts/w1/v1/w3-source-retirement.request.schema.json` (`031` was corrected to `035` because the integrated Alembic head was already `034`)
- [X] T042 [US3] Update the ORM deletion target constraint and typed store set in `backend/app/models/deletion.py`
- [X] T043 [US3] Stage one versioned W3 target/command inside confirmed owner deletion and permanent Source-retirement transactions, while excluding transient unavailable/unknown states, in `backend/app/services/deletion.py` and `backend/app/services/source_retirement.py`
- [X] T044 [US3] After W3-C adoption, implement the W1 side of the approved dedicated-SQS transport plus authenticated, schema/binding-checked, receipt-body-digest-idempotent `delete_owner`/`retire_source` receipt mapping and W1 acknowledge/failure transitions in `backend/app/runtime/outbox_relay.py`, `backend/app/runtime/w3_deletion_worker.py`, and `backend/app/runtime/w3_source_retirement_worker.py`
- [X] T045 [US3] Add one bounded authenticated receipt-worker entrypoint for both owner deletion and Source retirement with secret-safe count-only structured output in `backend/scripts/run_w3_retention_receipt_worker.py`; a single consumer owns the shared receipt queue so two processes cannot race or steal each other's operation
- [X] T046 [US3] Add least-privilege W1 retention command-relay and receipt-worker profiles in `backend/infra/w1-runtime.compose.yml`, keep relay-once/expire/inspect/redacted-backup on the single W3 state volume, and remove the obsolete assumption that direct operator deletion/retirement profiles satisfy W3-C
- [X] T046A [US3] Pin W3 implementation `66a0e3e1...`/receipt HEAD `bad8671...`, adopt its exact command/receipt schemas, add the authenticated bounded command-consumer profile on the same `/state/core.db` volume, and verify receipt-outbox-first restart behavior; actual Queue/Role values remain deployment-only inputs
- [X] T047 [P] [US3] Add bounded systemd service/timer templates for relay, expire, and HELD inspection in `backend/infra/systemd/epick-w3-core-runtime.service` and `backend/infra/systemd/epick-w3-core-runtime.timer`
- [X] T048 [US3] Implement HELD count detection, alert output, and an explicit prohibition on automatic replay in `backend/scripts/inspect_w3_core_runtime.py`
- [X] T049 [P] [US3] Document `w3.retention/1.1`, five-minute expire cadence, fifteen-minute logical-deletion SLO, Source-retirement rules, HELD ownership, manual replay authorization, quarantine backup lifecycle, and unsupported raw restore in `md/deploy/W1_W3_Runtime_Operations_Runbook_2026-09-20.md`
- [X] T050 [US3] With actual private Queue URLs/policies and W1/W3 stable Role IDs injected outside Git, run owner-deletion/Source-retirement duplicate/restart/race and lifecycle E2E tests and store count-only M3 results in `specs/007-w1-w3-runtime-integration/evidence/m3-deletion-operations.json`

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
       ├─ US2 / M2 [cutover: W3-A/B] ───────────┼─> US4 / M4 ──┐
       └─ US3 / M3 [dispatch closure: W3-C] ────┘              ├─> US5 / M5 [W3-F]
                                                 └─> Phase 8 Polish
```

- **Phase 1 → Phase 2**: Sequential; all stories rely on provenance, gate, and redaction utilities.
- **US1/M1**: Re-baselining starts immediately after Phase 2; W3-D is required only for final M1 closure.
- **US2/M2**: W1 Authority work T023–T032 starts after Phase 2; actual caller/client binding T033–T034
  waits for W3-A/B and does not require US1 to begin.
- **US3/M3**: W1 persistence/tests/operations work starts after Phase 2 because W3-E is verified.
  W3-C and T046A are complete against `66a0e3e1...`; T050 actual private queue/policy/role
  binding and live duplicate/restart E2E completed on 2026-09-21.
- **US4/M4**: Depends on completed US1 and US2 because both the deployed workload and actual
  currentness identity must be known.
- **US5/M5**: Depends on completed US1–US4 and W3-F.
- **Phase 8**: Runs after the milestones selected for delivery; final completion requires all stories.

### Within Each User Story

- The gate task runs first, records which local work may proceed, and stops actual cutover or closure
  at the exact task named by the phase when required inputs are absent.
- Tests are written and shown failing before implementation.
- Contract/model/migration work precedes services and runtime adapters.
- Preflight precedes AWS mutation or joint execution.
- Evidence validation precedes any milestone state transition to COMPLETE.

## Parallel Opportunities

- Phase 1 T002 and T003 can run in parallel after T001 establishes naming/pins.
- Phase 2 test tasks T004–T006 can run in parallel; T007–T009 then implement separate utilities.
- US1 tests T010–T013 and implementation files T014/T015/T019 are independent after re-baseline.
- US2 contract, DB, and HTTP tests T023–T025 can run in parallel now; only T033–T034 wait for W3-A/B.
- US3 migration, worker, lifecycle, and race tests T036–T040 can run in parallel; W1-side
  transport/receipt work T044–T049, T046A, and actual AWS runtime/deployment verification T050 are
  complete.
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

### User Story 2 W1-owned work

```text
Task T023: Authority JSON contract tests
Task T024: PostgreSQL currentness tests
Task T025: Private HTTP authentication/error tests
```

### User Story 3 W1-owned work after verified retention baseline

```text
Task T036: Migration test
Task T037: Deletion transaction test
Task T038: Deletion worker test
Task T039: Operations lifecycle test
Task T040: Cross-store race test
```

## Implementation Strategy

### MVP First — M1 Re-baseline

1. Complete Phase 1 and Phase 2.
2. Reopen stale M1 tasks and rebuild US1 from W3 implementation `66a0e3e1...`.
3. Send T021 to W3 for W3-D review.
4. Stop and validate M1; W1-owned M2/M3 work may continue, but do not guess W3-A/B/C/D/F.
5. Close M1 only after W3-D disposition is recorded.

### Incremental Delivery

1. **M1**: re-baselined immutable W3 runtime and durable single-host state.
2. **M2**: W1 Authority first, then actual caller/client cutover after W3-A/B.
3. **M3**: approved retention operations and durable owner deletion/Source retirement, with
   dispatcher closure at W3-C and verified actual private-queue duplicate/restart E2E.
4. **M4**: actual IAM/SQS binding after M1/M2.
5. **M5**: actual-producer joint CT-12 and teardown after all gates.

Each milestone retains independent evidence and can stop safely. Only M5 changes the integration
status to `JOINT_CT12_COMPLETE`.

## Notes

- Tasks T022 and T035 are scoped cutover stops; T051 and T059 are full phase stops, not paperwork-only checks.
- W3 domain code under `w3/Project_EPICK_Service/src/w3_knowledge/` is not modified under the pinned SHA. Required W3
  changes must arrive as a new W3-owned full SHA and pass provenance review.
- `W3_RETENTION_SECONDS=1209600` is a compatibility input required by `w3.retention/1.1`; the obsolete
  `3600` value must not appear in runtime commands or evidence.
- Actual AWS identifiers and secrets never enter these task descriptions or evidence files.
- `TRANSPORT_HANDOFF` proves SQS acceptance, not W1 application.
- Commit after each validated phase or coherent milestone; do not commit generated secrets/runtime DBs.
