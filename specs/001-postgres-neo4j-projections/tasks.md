---
description: "Dependency-ordered PostgreSQL implementation tasks for EPICK"
---

# Tasks: PostgreSQL Authoritative Foundation

**Input**: plan.md, research.md, data-model.md, quickstart.md, contracts/, PRD/W1/common
documents, and PostgreSQL implementation plan.  
**Scope**: PostgreSQL schema, persistence services, contract fixtures, and real-PostgreSQL tests.
This plan intentionally excludes public API endpoints, live SQS relay/worker activation, Neo4j,
Vector, egress, and AWS topology implementation.

**Non-negotiable boundaries**: PostgreSQL is the system of record. Applied Alembic revisions are
immutable and changes are forward-only. Private rows require owner-bearing relational constraints
and RLS. Job status is separate from completeness, required actions, and dispatch status. W3 draft
ACK/usability detail is prohibited until D-05 joint adoption.

**Tests**: Required. Each phase writes or extends real PostgreSQL and contract tests before the
corresponding implementation, in accordance with the constitution and integration checklist.

## Format

Every checklist item uses: checkbox, sequential ID, optional parallel marker, user-story label
where applicable, actionable description, and exact target path.

## Phase 1: Setup — PG-0 contracts and baseline audit

**Purpose**: Establish the actual migration starting point and freeze only the W1/W2/common v1
inputs needed for PostgreSQL Job/Outbox work. This phase does not apply a new RDS migration.

- [x] T001 [P] Audit local/shared/RDS Alembic head, identity schema, deletion-epoch presence, role privileges, and backup/restore evidence in specs/001-postgres-neo4j-projections/evidence/pg-0-baseline-audit.md
- [x] T002 [P] Add Draft 2020-12 schema-validation dependency and fixture-loading test support in backend/pyproject.toml and backend/tests/contract/conftest.py
- [x] T003 [P] Create immutable common v1 event-envelope schema plus redacted replay and gap fixtures in backend/contracts/common/v1/event-envelope.schema.json and backend/contracts/fixtures/v1/common/
- [x] T004 [P] Create W1 v1 Job, Job Command, Checkpoint, and deletion/fence schemas with acceptance/cancel/retry fixtures in backend/contracts/w1/v1/ and backend/contracts/fixtures/v1/w1/
- [x] T005 [P] Import the W2 one-Source Command, Result, and public Source Event schemas and fixtures from pinned runtime commit `0865ecd` without reinterpreting their payload in backend/contracts/w2/v1/ and backend/contracts/fixtures/v1/w2/
- [x] T006 Validate common, W1, and W2 schemas and all valid/invalid fixtures in backend/tests/contract/test_platform_contract_fixtures.py
- [x] T007 Record fixture versions, owners, C-01/C-02/C-05/C-07/C-08 status, and blocked external inputs in specs/001-postgres-neo4j-projections/evidence/pg-0-contract-freeze.md

**Checkpoint**: T001 through T007 are complete before Job/Outbox persistence is designed against
the v1 contracts. Public W2 Source Event consumer compatibility remains separately pending and
does not expand into W3 ACK or W4 usability contracts.

---

## Phase 2: Foundational — forward-only schema and runtime-role baseline

**Purpose**: Reconcile the current 000 to 002 Alembic chain with mandatory identity/deletion
invariants, then prove real PostgreSQL migration and RLS behavior. This phase blocks every user
story.

- [ ] T008 [P] Extend blank and pre-upgraded-database migration coverage, including forward-only corrective revision behavior, in backend/tests/integration/db/test_migrations.py
- [ ] T009 [P] Add failing identity/deletion-epoch, issuer/subject uniqueness, and existing-Experience regression cases in backend/tests/integration/db/test_identity_and_idempotency.py
- [ ] T010 [P] Define separate migrator, API runtime, worker, and deleter role grants without DDL or BYPASSRLS for runtime roles in backend/infra/postgres/runtime_roles.sql
- [ ] T011 Reconcile identity SQLAlchemy metadata and repository mapping with the audited issuer/subject semantics and deletion epoch in backend/app/models/identity.py and backend/app/repo/identity.py
- [ ] T012 Create the forward-only identity contract completion revision, including users.deletion_epoch and required constraints/indexes, in backend/migrations/versions/003_identity_contract_completion.py
- [ ] T013 Enforce transaction-local owner context and runtime-role connection assumptions without startup migration behavior in backend/app/db/session.py and backend/app/core/config.py
- [ ] T014 Verify migrator/runtime role separation, forced RLS, owner A/B isolation, and pooled-connection context cleanup in backend/tests/integration/db/test_rls_context.py
- [ ] T015 Run the PG-0 blank/existing migration and RLS suite, then record revision IDs and results in specs/001-postgres-neo4j-projections/evidence/pg-0-database-baseline.md

**Checkpoint**: A runtime principal can neither execute DDL nor bypass RLS, and the actual Alembic
head can upgrade safely. No later domain migration starts before this checkpoint.

---

## Phase 3: User Story 1 — Preserve personal work and accepted Jobs (Priority: P1) MVP

**Goal**: Persist owner-scoped application work, atomic Job acceptance, three actual execution
slots, immutable Snapshots, and selection history without relying on a projection service.

**Independent Test**: Create two users, save a Project and Question for one user, accept a
replay-safe Job, prove the fourth simultaneous claim never runs, create a Snapshot and selection,
then verify that the other user cannot link or read any of those records.

### P0-A: Application Workspace and Job acceptance

- [X] T016 [P] [US1] Write failing owner/project/question Version and cross-owner foreign-key tests in backend/tests/integration/db/test_application_workspace.py
- [X] T017 [P] [US1] Write failing atomic Job acceptance, idempotency replay/conflict, dispatch separation, slot claim, cancel-ack, and late fence/epoch result tests in backend/tests/integration/db/test_job_execution.py
- [X] T018 [P] [US1] Add Company, ApplicationProject, ProjectVersion, Question, and QuestionVersion ORM models with composite ownership keys in backend/app/models/application_workspace.py
- [X] T019 [P] [US1] Add Job, JobInputRef, JobRequiredAction, JobCommand, OutboxMessage, InboxReceipt, OwnerExecutionSlot, and JobExecutionLease ORM models in backend/app/models/jobs.py
- [X] T020 [US1] Create the workspace-and-Jobs forward migration with typed inputs, status checks, deferred current-pointer FKs, Outbox/Inbox, and slot/lease constraints in backend/migrations/versions/004_application_workspace_and_jobs.py
- [X] T021 [P] [US1] Implement owner-scoped Project/Question persistence and append-only Version creation in backend/app/repo/application_workspace.py
- [X] T022 [P] [US1] Implement Job/Command/Outbox and slot/lease locked persistence primitives in backend/app/repo/jobs.py
- [X] T023 [US1] Implement one-transaction acceptance, row-locked claim, fence invalidation, cancellation acknowledgement, and safe result-commit orchestration in backend/app/services/jobs.py
- [X] T024 [US1] Run the workspace/Job integration suite against real PostgreSQL and map CT-04 through CT-08 and CT-14 through CT-16 evidence in backend/tests/integration/db/test_job_execution.py

### P0-B: Snapshot, recommendation record, and material selection

- [ ] T025 [P] [US1] Write failing immutable Snapshot, same-owner/project membership, candidate eligibility, and ordered one-current-selection-set tests in backend/tests/integration/db/test_snapshot_and_selection.py
- [ ] T026 [P] [US1] Add Snapshot, SnapshotEpisodeVersion, RecommendationRun, RecommendationCandidate, MaterialSelectionSet, and MaterialSelectionItem ORM models in backend/app/models/recommendation.py
- [ ] T027 [US1] Create the Snapshot-and-selection forward migration with deferred active-Snapshot and typed Job-input foreign keys in backend/migrations/versions/005_snapshot_and_selection.py
- [ ] T028 [P] [US1] Implement owner-scoped Snapshot, Run, Candidate, and selection persistence primitives in backend/app/repo/recommendation.py
- [ ] T029 [US1] Implement immutable Snapshot creation and one-transaction material-selection orchestration in backend/app/services/recommendation.py
- [ ] T030 [US1] Run the P0 end-to-end storage suite and record Job, Snapshot, and selection evidence in specs/001-postgres-neo4j-projections/evidence/pg-p0-user-story-1.md

**Checkpoint**: User Story 1 is independently complete when a projection/worker outage cannot roll
back committed Project, Job, Snapshot, or selection records. This checkpoint does not activate a
worker or public API route.

---

## Phase 4: User Story 2 — Preserve reproducible knowledge and recommendation prerequisites (Priority: P2)

**Goal**: Add PostgreSQL-only Source/evidence lineage, public/private interpretation separation,
fixed analysis evidence, exclusions, projection readiness, and candidate-evidence persistence.
Actual Graph/Vector retrieval remains disabled.

**Independent Test**: Store a public Source Version and evidence, attach it privately through a
Job link, fix it into a Snapshot, record an exclusion and a delayed projection state, and prove
that owner scope, Snapshot membership, and current state prevent an invalid candidate record.

### P1-A: Company, Source, posting, and requirement lineage

- [ ] T031 [P] [US2] Write failing Source policy, Version/evidence lineage, collection-attempt, company-scope, and private Job-correlation tests in backend/tests/integration/db/test_company_source_evidence.py
- [ ] T032 [P] [US2] Add Company extension, Source, SourceVersion, EvidenceSpan, collection-attempt, JobSourceLink, organization/role Version, and source-decision ORM models in backend/app/models/company_knowledge.py
- [ ] T033 [US2] Create the company-and-Source lineage forward migration with append-only history and typed Job source links in backend/migrations/versions/006_company_sources.py
- [ ] T034 [US2] Implement policy-safe Source/evidence and private Job-link persistence without live egress in backend/app/repo/company_knowledge.py and backend/app/services/company_knowledge.py
- [ ] T035 [P] [US2] Write failing JobPosting/Requirement/CanonicalSkill company-scope and evidence-link tests in backend/tests/integration/db/test_job_postings_and_requirements.py
- [ ] T036 [P] [US2] Add CanonicalSkill, SkillAlias, JobPosting/Version, RequirementGroup, Requirement, and RequirementSkill ORM models in backend/app/models/job_postings.py
- [ ] T037 [US2] Create the posting-and-requirement forward migration, adding P1 foreign keys only with their parent tables, in backend/migrations/versions/007_job_postings_and_requirements.py
- [ ] T038 [US2] Implement posting, requirement, and skill-link persistence with no invented requirement fact in backend/app/repo/job_postings.py

### P1-B: Claim, interpretation, question-analysis, and Snapshot evidence

- [ ] T039 [P] [US2] Write failing public/private interpretation separation, Claim evidence, primary-intent, and fixed Snapshot-analysis tests in backend/tests/integration/db/test_analysis_and_snapshot_evidence.py
- [ ] T040 [P] [US2] Add Claim/Version/Evidence, public Interpretation, private ProjectInterpretation, QuestionAnalysis, Intent, ModelExecution, and Snapshot-evidence ORM models in backend/app/models/analysis.py
- [ ] T041 [US2] Create the claim-and-interpretation forward migration with physically separate public and private relations in backend/migrations/versions/008_claims_and_interpretations.py
- [ ] T042 [US2] Create the question-analysis-and-Snapshot-evidence forward migration in backend/migrations/versions/009_question_analysis_and_snapshot_evidence.py
- [ ] T043 [US2] Implement evidence-backed persistence that stores safe execution metadata but never prompt/response or hidden reasoning in backend/app/services/question_analysis.py

### P1-C: Outbox, exclusions, projection state, and candidate evidence ledger

- [ ] T044 [P] [US2] Write failing Outbox atomicity, public/private payload-redaction, Inbox dedup, delayed-revision, projection-outage, and Snapshot-exclusion tests in backend/tests/integration/db/test_projection_readiness.py
- [ ] T045 [P] [US2] Add ProjectionSyncState, ExperienceExclusion, and SnapshotExclusion ORM models in backend/app/models/projection.py
- [ ] T046 [US2] Create the projection-state-and-exclusion forward migration using the existing outbox_messages boundary in backend/migrations/versions/010_projection_state_and_exclusions.py
- [ ] T047 [US2] Implement same-transaction authoritative mutation plus Outbox insertion, relay-row claim primitives, Inbox receipt, and monotonic state updates without live SQS or Neo4j calls in backend/app/services/projection_outbox.py
- [ ] T048 [P] [US2] Write failing candidate-evidence, assessment, warning, retrieval-metadata, comparison, and run-qualification relational tests in backend/tests/integration/db/test_candidate_evidence.py
- [ ] T049 [P] [US2] Add candidate evidence, assessment, warning, comparison, and run-qualification ORM models in backend/app/models/candidate_evidence.py
- [ ] T050 [US2] Create the candidate-evidence forward migration without creating a Graph/Vector retrieval adapter in backend/migrations/versions/011_candidate_evidence.py
- [ ] T051 [US2] Run the P1 PostgreSQL-only evidence, Outbox, exclusion, and candidate-ledger suite and record integration-checklist coverage in specs/001-postgres-neo4j-projections/evidence/pg-p1-user-story-2.md

**Checkpoint**: P1 records are reproducible and projection-ready, but no Graph/Vector route or W3
consumer is enabled.

---

## Phase 5: User Story 4 — Future Engine connection remains deferred (Priority: P1)

**Goal**: Protect the PostgreSQL plan from accidentally treating W3 draft behavior as an adopted
integration contract.

**Independent Test**: Before D-05 adoption, the migration catalog and service layer expose no W3
typed ACK/usability persistence or live Graph/Vector route; a future D-05 addition must introduce
a versioned fixture and additive migration first.

- [ ] T052 [US4] Add a regression test that rejects W3 typed ACK/usability persistence and route activation before an adopted D-05 fixture is present in backend/tests/contract/test_w3_contract_hold.py
- [ ] T053 [US4] After and only after joint D-05 adoption, record the approved schema owner, fixture paths, compatibility evidence, and additive-migration requirement in specs/001-postgres-neo4j-projections/evidence/w3-d05-adoption.md

**Checkpoint**: T053 is externally blocked today. It is not authorization to create W3 fields,
tables, endpoints, or worker wiring.

---

## Phase 6: User Story 3 — Complete deletion without residual private data (Priority: P3)

**Goal**: Implement the P2 decision, recovery, privacy, and deletion ledger so private work is
fenced by deletion epoch and cannot be marked deleted until each required target reports the same
epoch.

**Independent Test**: Begin a synthetic deletion for one owner while a worker result is delayed;
prove the deletion epoch invalidates the result, incomplete targets keep deletion non-terminal,
and public Company records remain outside the private deletion scope.

### P2-A: Decision trails, checkpoints, and notifications

- [ ] T054 [P] [US3] Write failing append-only inference/duplicate decision, explicit merge, checkpoint version/fence/epoch, retry, and safe-notification tests in backend/tests/integration/db/test_inference_and_job_recovery.py
- [ ] T055 [P] [US3] Add inference, duplicate-decision, merge-record, checkpoint, and Notification ORM models in backend/app/models/lifecycle_operations.py
- [ ] T056 [US3] Create the inference-and-duplicate-decision forward migration in backend/migrations/versions/012_inference_and_duplicates.py
- [ ] T057 [US3] Create the job-recovery-and-notification forward migration in backend/migrations/versions/013_job_recovery_and_notifications.py
- [ ] T058 [US3] Implement approval-gated merge, checkpoint persistence, idempotent recovery, and safe notification orchestration in backend/app/services/lifecycle_operations.py

### P2-B: Settings, consent, feedback, and sensitivity

- [ ] T059 [P] [US3] Write failing preference inheritance, fixed Snapshot preference, consent, analytics opt-in, sensitivity-decision, and external-processing-fence tests in backend/tests/integration/db/test_privacy_controls.py
- [ ] T060 [P] [US3] Add settings, preferences, consent, retention, feedback, analytics, sensitivity assessment/finding/decision ORM models in backend/app/models/privacy_controls.py
- [ ] T061 [US3] Create the settings-consent-feedback forward migration in backend/migrations/versions/014_user_settings_and_feedback.py
- [ ] T062 [US3] Create the sensitivity-privacy forward migration with location-only findings in backend/migrations/versions/015_sensitivity_privacy.py
- [ ] T063 [US3] Implement restrictive current-decision checks for external-processing eligibility in backend/app/services/privacy_controls.py

### P2-C: Deletion orchestration

- [ ] T064 [P] [US3] Write failing deletion confirmation, partial target completion, target-only retry, deletion-epoch/fence invalidation, delayed-result, and restore-replay tests in backend/tests/integration/db/test_deletion_orchestration.py
- [ ] T065 [P] [US3] Add DeletionRequest and DeletionTarget ORM models with typed target state and acknowledgement epoch in backend/app/models/deletion.py
- [ ] T066 [US3] Create the deletion-orchestration forward migration in backend/migrations/versions/016_deletion_orchestration.py
- [ ] T067 [US3] Implement one-transaction epoch advance, active-Job fence invalidation, private deletion Outbox creation, target retry, and completion fencing in backend/app/services/deletion_orchestration.py
- [ ] T068 [US3] Run the P2 lifecycle/deletion suite with private-consumer fakes and record remaining external target evidence in specs/001-postgres-neo4j-projections/evidence/pg-p2-user-story-3.md

**Checkpoint**: PostgreSQL never reports complete deletion until all typed private targets acknowledge
the current epoch. Live Neo4j/Vector/Cache deletion remains a separate later Gate.

---

## Phase 7: Polish and release-readiness evidence

**Purpose**: Validate the complete PostgreSQL migration catalog and leave a handoff that does not
misrepresent unrun worker, egress, W3, or RDS production evidence.

- [ ] T069 [P] Run blank/existing DB upgrade paths, approved downgrade checks, RLS isolation, ownership, Version, Snapshot, Job, Outbox, and deletion regressions in backend/tests/integration/db/
- [ ] T070 [P] Run common/W1/W2 fixture validation and the W3-hold regression suite in backend/tests/contract/
- [ ] T071 [P] Validate only observed local commands and prerequisites in specs/001-postgres-neo4j-projections/quickstart.md
- [ ] T072 Update migration IDs, passed integration-checklist rows, external Gate status, and unverified RDS facts in specs/001-postgres-neo4j-projections/evidence/postgresql-release-readiness.md
- [ ] T073 Produce the PostgreSQL implementation handoff with changed revisions, test evidence, rollback/forward-fix guidance, and explicitly deferred W3/AWS/API items in backend/docs/agents/POSTGRESQL_IMPLEMENTATION_REPORT.md

---

## Dependencies and execution order

    T001–T007  PG-0 contract/audit
        ↓
    T008–T015  forward-only baseline and RLS
        ↓
    T016–T030  US1 P0 workspace, Job, Snapshot, selection
        ↓
    T031–T051  US2 P1 source/evidence, Outbox, exclusions, candidate ledger
        ↓
    T054–T068  US3 P2 lifecycle and deletion
        ↓
    T069–T073  release-readiness evidence

- User Story 1 is the MVP and blocks every dependent P1/P2 relation.
- User Story 2 depends on User Story 1 because Source links, Snapshot, and Outbox reference P0
  parents. It does not activate actual retrieval.
- User Story 3 depends on User Story 1 and the generic Outbox boundary from User Story 2.
- User Story 4 is an external D-05 Gate. T052 is a guard; T053 remains blocked until joint
  adoption and does not block PostgreSQL P0/P1/P2 persistence work.

## Parallel opportunities

- T001–T005 are independent planning/fixture tasks; T006 validates their combined output.
- Within US1, T016/T017 and T018/T019 may proceed in parallel; migrations wait for the tests and
  model decisions they cover.
- Within US2, test/model pairs T031/T032, T035/T036, T039/T040, T044/T045, and T048/T049 can
  proceed in parallel after their prerequisite revision is at head.
- Within US3, test/model pairs T054/T055, T059/T060, and T064/T065 can proceed in parallel.
- No task may run a later migration before the immediately preceding migration's real PostgreSQL
  exit suite passes.

## Implementation strategy

### MVP first

1. Complete PG-0 and foundational baseline.
2. Complete User Story 1 through T030.
3. Demonstrate durable personal work, Job acceptance, slot enforcement, Snapshot immutability,
   and selection persistence with no worker, API route, or projection dependency.
4. Stop and review the evidence before P1.

### Incremental delivery

1. Add Source/evidence and generic projection readiness through T051 while keeping W3 and
   Graph/Vector disabled.
2. Add P2 lifecycle/deletion through T068 using private-consumer fakes only.
3. Use T069–T073 to prepare the RDS migration handoff.
4. Start API implementation, worker/SQS activation, AWS expansion, or W3 integration only from
   their dedicated future plans and Gates.

## Explicitly not scheduled

- A W3 ACK/event schema, status enum, usability DTO, cache contract, or typed ledger
- Live SQS, Linux worker, egress, Neo4j, Vector, W4, or public FastAPI route implementation
- RDS migration application, production credential changes, or AWS topology changes

Every task above follows the required checklist format.
