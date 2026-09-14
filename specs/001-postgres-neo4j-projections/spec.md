# Feature Specification: Staged Data and Search Projections

**Feature Branch**: `not-created` (Spec Kit workspace: `001-postgres-neo4j-projections`)

**Created**: 2026-09-12

**Status**: Draft

**Input**: User description: "DB (PostgreSQL), Neo4j 단계별 구현 계획 수립"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve personal work during search-service outages (Priority: P1)

As a job seeker, I can save and continue working with my experiences and application projects
even when the search projection service is unavailable, so that a search-system outage never
loses or rolls back my confirmed work.

**Why this priority**: Preserving user-entered experience and prior decisions is the primary
product integrity requirement and must work before any search capability is enabled.

**Independent Test**: Save an experience version and an application snapshot while the search
projection is unavailable; reopen the project and verify the saved versions remain available and
the projection work is marked for later recovery.

**Acceptance Scenarios**:

1. **Given** a user saves a new experience version while its search projection is unavailable,
   **When** the save completes, **Then** the user can retrieve the new version and the outstanding
   projection work is visible as pending or failed without changing the saved version.
2. **Given** an older projection update arrives after a newer version is saved, **When** recovery
   processes the updates, **Then** the newer version remains the current searchable version.

---

### User Story 2 - Receive privacy-safe, reproducible candidate references (Priority: P2)

As a job seeker, I receive candidate experience references only from my authorized snapshot and
not from another user's experiences, so that candidate discovery is useful without compromising
privacy or changing the meaning of my historical project.

**Why this priority**: Candidate discovery is the central service value, but it is safe only after
the authoritative-record and recovery foundation is in place.

**Independent Test**: Create equivalent experience data for two users, run candidate discovery
for one user's fixed snapshot, and verify that only the authorized, non-excluded experience
versions are returned with their discovery paths.

**Acceptance Scenarios**:

1. **Given** two users have experiences connected to the same skill, **When** one user requests
   candidate discovery, **Then** the result never contains the other user's experience reference.
2. **Given** an experience is excluded in a snapshot, **When** any discovery path finds it,
   **Then** it is not offered as a default candidate for that snapshot.
3. **Given** a candidate is found through more than one discovery path, **When** results are
   prepared for evaluation, **Then** the candidate is represented once while its paths remain
   traceable.

---

### User Story 3 - Complete deletion without residual personal search data (Priority: P3)

As a user who requests complete deletion, I can see that my personal records and derived search
representations are removed before deletion is marked complete, while shared company information
needed by other users remains available.

**Why this priority**: Deletion and recovery complete the privacy lifecycle after reliable storage
and candidate discovery are available.

**Independent Test**: Submit a complete deletion request for a test user, verify each required
storage target reports completion, and confirm the user's experience cannot be rediscovered while
public company information remains accessible.

**Acceptance Scenarios**:

1. **Given** one required deletion target has not completed, **When** deletion status is queried,
   **Then** it is not reported as complete.
2. **Given** complete deletion finishes, **When** the user's prior candidate search is repeated,
   **Then** no personal experience reference is returned.

---

### User Story 4 - Connect a compatible future Engine safely (Priority: P1)

As a platform operator, I can connect a later Engine release only when its declared contract,
Graph schema, and enabled retrieval capabilities are compatible, so that an Engine upgrade never
changes authoritative Service data or silently weakens search isolation.

**Why this priority**: The Engine is a separate boundary and will evolve independently. A
versioned, fail-closed contract is required before either system can be deployed or rolled back
independently.

**Independent Test**: Run the Service against an Engine that advertises a supported contract, then
against one that advertises an unsupported contract or Graph schema. The supported pairing applies
an idempotent projection; the incompatible pairing leaves PostgreSQL data intact and records a
safe compatibility failure without enabling the affected search route.

**Acceptance Scenarios**:

1. **Given** an Engine supports the Service's projection and DTO contract versions,
   **When** the Dispatcher delivers an Outbox record, **Then** the Engine applies or safely
   acknowledges the Version without duplicate or regressed projections.
2. **Given** an Engine does not support the required contract or Graph schema version,
   **When** the Dispatcher evaluates its capabilities, **Then** it does not send incompatible work,
   records `CONTRACT_INCOMPATIBLE`, and leaves the authoritative PostgreSQL mutation successful.
3. **Given** a new incompatible Engine contract is introduced,
   **When** it is rolled out, **Then** the Engine supports both contracts before the Service starts
   producing the new version, and the previous contract remains available through rollback.

### Edge Cases

- A retry delivers the same change record more than once or delivers an older version after a
  newer version; recovery must not create duplicate or stale searchable representations.
- A user changes an experience, revokes its use, or makes a sensitivity decision while recovery is
  pending; the most restrictive current permission must govern external search use.
- A complete deletion request is interrupted after some storage targets finish; retry resumes only
  unfinished targets and never restores deleted personal search data.
- Candidate discovery finds a result through a strong route but its authoritative snapshot,
  ownership, version, or exclusion check no longer matches; the result is withheld.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST introduce the work in ordered stages: authoritative-data integrity,
  shared knowledge and searchable projections, candidate discovery, then lifecycle recovery and
  deletion completion.
- **FR-002**: The system MUST preserve immutable historical versions and snapshots when newer
  experience, project, or knowledge information is saved.
- **FR-003**: The system MUST keep authoritative personal records available when a derived search
  representation is delayed, unavailable, or fails to update.
- **FR-004**: The system MUST prevent a user from discovering, reading, changing, or selecting
  another user's personal experience through any discovery path.
- **FR-005**: The system MUST ensure that a candidate reference is valid only when its ownership,
  fixed snapshot, version, permitted-use state, and exclusion state all match the active request.
- **FR-006**: The system MUST preserve the route or routes that found a candidate without treating
  route count or route score as evidence of user ability, qualification, or hiring likelihood.
- **FR-007**: The system MUST process duplicate, delayed, and out-of-order projection updates
  without replacing a newer searchable representation with an older one.
- **FR-008**: The system MUST keep public company knowledge separate from personal experiences,
  approvals, and project-specific interpretations.
- **FR-009**: The system MUST mark personal deletion complete only after every required personal
  record and derived search representation has completed deletion; shared company knowledge MUST
  remain unaffected.
- **FR-010**: The system MUST provide automated verification for every stage's ownership,
  versioning, snapshot, retry, deletion, and failure-recovery boundaries before the next stage is
  enabled.
- **FR-011**: The Service↔Engine boundary MUST use separately versioned projection-event,
  canonical-DTO, candidate-reference, Graph-schema, and Vector-index contracts. A resource's
  immutable Version MUST NOT be used as a substitute for an interface-contract version.
- **FR-012**: The Service MUST activate or dispatch to an Engine only after it verifies the
  Engine's declared supported contract versions, Graph schema version, and route capabilities.
  Incompatibility MUST fail closed for the affected projection or retrieval route without rolling
  back PostgreSQL source data, Snapshots, or user decisions.

### Key Entities *(include if feature involves data)*

- **Authoritative record**: The user-owned or shared source of truth for versions, approvals,
  snapshots, and deletion state.
- **Search projection**: A rebuildable representation derived from an authoritative record for
  relationship or semantic discovery.
- **Projection change record**: A traceable request to create, update, invalidate, retry, or
  remove a search projection.
- **Application snapshot**: The immutable set of allowed experience and knowledge versions used
  for one analysis.
- **Candidate reference**: A discovery result that identifies an allowed experience version and
  the path by which it was found; it is not a qualification decision.
- **Deletion request**: A user-confirmed process that tracks removal of personal records and every
  derived storage target.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of automated ownership-isolation scenarios prevent one test user from receiving
  another test user's personal experience reference.
- **SC-002**: 100% of automated retry and out-of-order scenarios preserve the newest authorized
  version as the current searchable representation.
- **SC-003**: 100% of automated snapshot scenarios return only experience versions included in the
  requested snapshot and not excluded for that request.
- **SC-004**: 100% of complete-deletion scenarios remain incomplete until every required personal
  storage target reports completion, and return zero deleted personal references afterward.
- **SC-005**: Each implementation stage has a runnable integration scenario demonstrating that a
  failure in derived search processing does not prevent users from retrieving saved work.
- **SC-006**: 100% of automated Service/Engine compatibility scenarios either apply a supported
  contract version or reject an unsupported version before Engine invocation, while preserving the
  authoritative PostgreSQL mutation and disabling only the affected derived route.

## Assumptions

- Existing PRD, platform, database, and graph contracts remain authoritative; this feature plans
  their implementation order and does not change their approved semantics.
- The project will keep a single authoritative relational record and rebuild derived search data
  from versioned source records.
- The accepted Engine integration baseline is recorded in
  [ADR-001](decisions/ADR-001-engine-integration-baseline.md): Queue plus internal HTTP, pinned
  Neo4j deployment baseline, and W3 Graph-schema ownership are selected. Actual embedding-model
  values and operational limits remain externally verified G-02/G-04/G-07 inputs.
- Service↔Engine transport can change behind the internal ports, but every transport MUST preserve
  the approved versioned envelope, canonical DTO, capability, and result contracts.
- The work is limited to staged backend data and search-projection implementation; frontend,
  writing-assistant, and unrelated product features remain out of scope.
