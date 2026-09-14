# ADR-001: Engine Integration Baseline and Change Boundaries

**Status**: Accepted for the current implementation plan

**Decision source**: Product owner direction on 2026-09-12

**Scope**: This ADR fixes the Service↔future Engine integration baseline. It does not expose a
public API, create an Engine implementation in this repository, or override the remaining
external-provider and operational verification gates.

## Context

`Project_EPICK_Service` owns authoritative PostgreSQL data. `Project_EPICK_Engine` will later own
Neo4j Projection and candidate-reference retrieval. The two repositories must be independently
deployable and rollbackable without an Engine mismatch altering source records, Snapshots, user
decisions, ownership boundaries, or public API contracts.

## Accepted Decisions

| Gate | Current decision | Fixed now | Verification or value still open |
|---|---|---|---|
| G-02 | Use the provider's query/passage pair. Record the actual returned Vector dimension without manual selection; limit each application chunk to at most 80% of the provider-verified input limit. First PoC observes request count, input amount, and 429 only as safe metadata. Budget ceilings are operational configuration. | Query/passage role separation, 80% guardrail, safe observability, and no raw text/vector/key logging. | Actual model IDs, returned dimension, provider input limit, price, rate limit, external-processing permission, and final budget values require a key-backed smoke test and G-04 review. |
| G-03 / G-N3 | Use both Queue and internal HTTP: PostgreSQL Transactional Outbox is the authoritative event source; a Service-owned dispatcher publishes a minimal event to a durable Queue; the Engine obtains the versioned canonical Projection DTO and reports its safe result through internal-only HTTP Ports. | PostgreSQL source authority, Queue+HTTP topology, minimal event payload, DTO/result Port boundaries, and Service ownership of delivery state. | Concrete Queue product, credentials, timeout, concurrency, retry schedule, and DLQ retention are operational settings. They must preserve the fixed logical contracts. |
| G-N1 | Pin Neo4j Community Edition image `neo4j:2026.08.0` and use Cypher 25. Run it in a private Docker network with Service/Engine; do not expose Bolt or Browser to the frontend or public Internet. Deploy with persistent storage and rebuild from PostgreSQL when needed. | Exact image/edition, private-network boundary, no `latest`, no public Neo4j access, and PostgreSQL-led rebuild. | Capacity, host/cloud provider, backup topology, and a real owner/active/index-version Vector filter smoke test remain operational validation items. Vector remains disabled until the smoke test passes. |
| G-10 / G-N2 | W3 Knowledge/Indexing is the single logical owner of Graph Schema, Projection DTO, schema version, and approved relationship allowlist. W4 consumes the Retrieval Port and cannot independently change Graph shape. | Logical owner and change authority. | The named maintainer is assigned when the Engine repository/team is created. Until then, the current Backend/AI maintainer acts as the W3 owner. |

## Service-to-Engine Flow

```text
PostgreSQL mutation + Outbox INSERT (one transaction)
  → Service dispatcher leases the event
  → durable Queue carries only event ID/type/resource/version
  → Engine consumes the event
  → internal HTTP: Engine reads canonical versioned Projection DTO from Service
  → idempotent Neo4j Projection
  → internal HTTP: Engine reports safe result code to Service
  → Service updates projection_sync_state and retry/DLQ state
```

The Queue is not a source of truth. Internal HTTP is not a public API. Event payloads and DTOs
are different contracts: events contain safe identifiers and state codes only, while DTO access is
versioned and authenticated. Neither carries raw user text, prompt/response, embedding vectors,
or credentials in logs, Outbox payloads, or Queue messages.

## Responsibility Split

| Participant | Responsibility |
|---|---|
| Service writer | PostgreSQL mutation and same-transaction Outbox insertion. |
| Service dispatcher | Lease/publish, retry schedule, DLQ isolation, capability check, and `projection_sync_states`. |
| Engine | Validate contract and DTO, apply idempotent `MERGE`, enforce resource-version monotonicity, and return an allowlisted result code. |
| W3 owner | Approve Graph schema, DTO, relationship, schema-version, and compatibility changes. |
| W4 | Request new retrieval data through the contract; never create parallel Graph labels, relationships, or DTO fields. |

`APPLIED`, `ALREADY_APPLIED`, and `SUPERSEDED` converge successfully. Only
`RETRYABLE_ERROR` is automatically retried with configured backoff and jitter.
`REJECTED_CONTRACT`, `REJECTED_DATA`, and `CONTRACT_INCOMPATIBLE` are isolated for correction and
explicit re-drive; they must not retry indefinitely.

## Compatibility and Change Policy

Four independent versions remain separate:

| Version | Change purpose |
|---|---|
| `projection_event_contract_version` | Queue/HTTP Projection-event envelope. |
| `projection_dto_contract_version` / `candidate_ref_contract_version` | Internal HTTP DTO and retrieval-reference shape. |
| `resource_version` | Immutable PostgreSQL record ordering; never an interface-version substitute. |
| `graph_schema_version` / `vector_index_version` | Neo4j Graph layout and embedding-space compatibility. |

- An optional additive field is a new minor contract and is emitted only after Engine capability
  confirms support.
- Field removal, semantic/enum reinterpretation, ownership/deletion behavior change, or Graph
  relationship meaning change is a new major contract or schema version.
- Deploy Engine support for old and new contracts first; run the compatibility matrix; switch the
  Service producer; drain/replay the old backlog; retain old Engine support through the Service
  rollback window.
- A Queue product change, internal HTTP implementation change, Neo4j patch update, or embedding
  model change stays behind the same ports. It cannot change the public FastAPI API or PostgreSQL
  source schema implicitly.
- An embedding-space change creates a new `vector_index_version`; use dual read, full reindex,
  validation, cutover, then retirement. Never mix Vector spaces under one version.

## Required Compatibility Evidence

1. Engine capability profile declares the supported event, DTO, CandidateRef, Graph-schema, and
   route versions before dispatch or route activation.
2. Service N/Engine N-1, Service N/Engine N, and Engine-first dual-support rollout pass the same
   synthetic contract fixtures.
3. An unsupported contract, schema, or route is rejected before Engine invocation; PostgreSQL
   source data and Snapshot reads remain successful.
4. Duplicate and reverse-order events, worker restart after Engine write, delete/revoke followed
   by delayed upsert, and User A/B shared-Skill retrieval preserve idempotency and isolation.
5. The G-02 smoke test records the actual provider model IDs, Vector dimension, input limit,
   query/passage compatibility, and 429 behavior without exposing the API key or user text.
6. The G-N1 smoke test proves Vector filtering inside the selected index for `owner_user_id`,
   active state, and `vector_index_version`; until then, only Graph+Alias routes may activate.

## Consequences

- PostgreSQL P0/P1 and Outbox work can proceed before an Engine repository exists.
- Graph+Alias can activate only after the declared capability profile and compatibility tests pass.
- Vector remains a conditional route even with the pinned Neo4j image, because model and filter
  behavior are externally verified facts rather than assumptions.
- A future Engine conflict becomes an observable, isolated compatibility failure rather than a
  cross-repository data or API break.
