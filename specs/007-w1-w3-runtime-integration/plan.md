# Implementation Plan: W1–W3 Actual Runtime Integration

**Branch**: `007-w1-w3-runtime-integration` | **Date**: 2026-09-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/007-w1-w3-runtime-integration/spec.md`

## Summary

Turn the locally verified W3 Core runtime at full SHA
`c7e6788168c048941bdabe7ed8cb01007edeecec` into an immutable, single-host deployment and connect
it to W1's authoritative Job/deletion state without changing the adopted W3 event wire. M1 builds
and verifies the non-root/read-only W3 image plus durable `/state/core.db`. M2 connects the actual
AnalysisPlan caller and a minimal authenticated W1 Authority boundary. M3 adds W3 as an explicit
durable deletion target and deploys approved relay/expire/HELD operations. M4 binds an actual
send-only W3 IAM role to the existing W1 receive-only SQS boundary. M5 runs actual-producer joint
CT-12 and records count reconciliation and teardown. Only M1 can start now; later milestones retain
their W3/approval gates rather than inventing missing values.

## Technical Context

**Language/Version**: W1 Python >=3.10; W3 image Python >=3.12 from its locked `uv.lock`

**Primary Dependencies**: FastAPI 0.141+, Pydantic/Pydantic Settings 2.x, SQLAlchemy 2.x,
Alembic 1.19+, psycopg 3.x, W3 `w3-knowledge`/Pydantic 2.12+/boto3 1.43+, Docker Engine with
Compose v2, AWS ECR/SQS/IAM/STS

**Storage**: W1 PostgreSQL 16 remains authoritative; W3 owns a transport-local SQLite
`/state/core.db`; Amazon SQS Standard Main Queue/DLQ carries the adopted event; ECR stores the
immutable W3 image

**Testing**: pytest contract/unit/PostgreSQL integration tests, W3 locked smoke/core tests,
Docker image/Compose policy tests, isolated actual AWS preflight, and joint CT12-01~12

**Target Platform**: Linux Docker runtime on the existing private Worker EC2 in
`ap-northeast-2`; one host and one named state volume

**Project Type**: FastAPI backend plus private queue workers and a separately packaged W3 CLI runtime

**Performance Goals**: Keep each Authority request bounded by an explicit timeout; preserve W3's
3-second connect/5-second read AWS timeout and one SDK attempt; process one due W3 delivery per
`relay-once` transaction; never hold an unbounded retry loop or automatic HELD replay

**Constraints**: Existing `w3.private.core-decision/0.1-candidate` wire is immutable; PostgreSQL is
the W1 source of truth; W3 SQLite is single-host only; `BEGIN IMMEDIATE` serializes W3 writes;
root filesystem is read-only; credentials and owner identifiers do not enter Git/image/logs;
raw SQLite/volume/snapshot restore is unsupported; M2–M5 external gates remain explicit

**Scale/Scope**: One actual W3 runtime deployment, one W1 private Authority adapter, one additional
W3 deletion target/dispatcher, one actual send-only IAM role, one Main/DLQ binding, and one joint
CT-12 evidence run; W2, W4 and Neo4j work remain out of scope

## Constitution Check

*GATE: Passed before Phase 0 and re-checked after Phase 1.*

- **FastAPI Backend Standard — PASS**: M2's W1 Authority boundary uses isolated schema/service/repo
  layers and a private FastAPI adapter. Business currentness does not live only in a route.
- **PostgreSQL Is the System of Record — PASS**: W1 Job, owner, deletion epoch and deletion dispatch
  remain authoritative in PostgreSQL with Alembic changes. W3 SQLite is an external component's
  local delivery ledger, never a replacement for W1 application state.
- **Tests Are Required — PASS**: Each milestone has contract, failure, persistence and integration
  tests. M5 requires actual W3/SQS/W1 execution, not mocks alone. All fixtures are synthetic.
- **Existing Contracts Are Preserved — PASS**: The adopted W3 wire and W1 inbound behavior are not
  changed. New Authority and deletion contracts are private, versioned and gated on W3 review.
- **Gated values remain gated — PASS**: actual caller/adapter ownership, retention duration, W3
  workload identity and joint window are not fabricated. Only M1 is currently executable.

### Post-design re-check

Phase 1 retains the same boundaries. The Authority contract returns only W3's existing
`Authorization` fields. Deletion is integrated through W1's existing PostgreSQL deletion workflow
instead of direct SQLite mutation from the API transaction. Runtime readiness and CT-12 evidence
contracts distinguish build/smoke proof from actual service completion. No gated production value
is assigned a placeholder that could be mistaken for READY.

## Design Decisions

1. Keep this work in a new feature rather than overwrite implemented inbound feature 003.
2. Build W3 from its pinned source and lockfile; publish by commit tag and deploy by ECR manifest digest.
3. Run W3 as non-root with read-only rootfs, bounded `/tmp` tmpfs, and exactly one RW named volume
   mounted at `/state`; all commands use `/state/core.db`.
4. Use W1 PostgreSQL for authoritative currentness and deletion. W3 SQLite stores only W3 event,
   delivery, counter and deletion-suppression state.
5. Define the minimal Authority request/response now, but do not finalize its route authentication,
   client adapter or deployment until W3-A/B names the actual caller and adapter owner.
6. Extend W1's existing deletion target/outbox workflow with an explicit W3 target after W3-C
   confirms transport and acknowledgement mapping. Do not call Docker or SQLite from an API transaction.
7. Run relay/expire/inspect as bounded one-shot operations under W1-managed scheduling. `HELD`
   generates an alert and requires W3/operator review; it never causes automatic replay.
8. Reuse the established W3 Core Decision Main Queue/DLQ boundary, replacing only the synthetic
   T043 sender with the actual W3 workload role. Queue policy names the exact role principal.
9. Treat `TRANSPORT_HANDOFF` as SQS acceptance only. W1 durable application is proven separately by
   receipt/decision/binding counts.
10. End integration only at M5 after actual W3 production, count reconciliation, race/failure cases
    and cleanup are recorded in one immutable evidence manifest.

Detailed rationale and rejected alternatives are in [research.md](research.md).

## Milestone Plan

| Milestone | Inputs / gate | W1 work | Joint or external work | Exit evidence |
| --- | --- | --- | --- | --- |
| **M1 — immutable image·single-host state** | W3 source pin is available; no external wait to start | Add W3 Dockerfile/Compose, image policy tests, ECR recipe, `/state` volume/env/preflight and restart smoke | W3-D reviews entrypoint, shared SQLite, non-root/read-only layout before final digest | source SHA + manifest digest; locked build; smoke; state persistence after container recreation; W3-D disposition |
| **M2 — actual supplier·Authority** | **GATE: W3-A/B** | Add private Authority schema/service/read-only DB grants/adapter/preflight | actual analysis owner pins caller SHA/function/semantics/idempotency; W3 implements client adapter and timeout/error mapping | valid/current succeeds; source/cancel/delete/epoch mismatch and 401/403/404/409/503/timeout fail closed |
| **M3 — deletion·operations** | **GATE: W3-C/E + product/privacy retention approval** | Add W3 deletion target/migration, durable dispatch routing, ack/failure reconciliation, scheduler, inspect/HELD alarm/runbook | W3 confirms delete-owner adapter/idempotency/inspect mapping; approver supplies revisioned retention/tombstone policy | deletion-first/send-first races; duplicate/restart retry; other-owner unaffected; approved runner/HELD evidence |
| **M4 — actual IAM/SQS** | M1 and M2 complete; actual compute identity known | Create exact send-only role/policy, queue resource policy, W1 receive-only policy, root-only env and two-sided preflight | W3 workload proves default credential chain and stable role ID self-check | Role ARN/Role ID captured securely; W3 role ID = W1 expected SenderId; negative permissions denied |
| **M5 — joint CT-12** | M1–M4 + **GATE: W3-F** | Provision isolated DB/SQS window, run W1 consumer, capture DB/SQS counts and teardown | actual W3 caller/supplier/outbox/relay runs all scenarios and supplies inspect/digest evidence | CT12-01~12 pass; counts reconcile; no auto command; new fence exactly once; stale effects zero; cleanup manifest |

M1 may be implemented immediately. M1's source/recipe/smoke can be completed before W3-D, but the
final production-ready digest must not be declared closed until W3-D is recorded. M2–M5 tasks must
remain blocked at their stated gates even if scaffolding or synthetic tests can be written earlier.

## Project Structure

### Documentation (this feature)

```text
specs/007-w1-w3-runtime-integration/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    ├── README.md
    ├── authority-request.schema.json
    ├── authority-response.schema.json
    ├── deletion-command.schema.json
    ├── runtime-readiness.schema.json
    └── joint-ct12-scenarios.md
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── api/schemas/                 # private Authority request/response schemas if shared
│   ├── core/config.py               # W3 Authority/deletion/runtime settings
│   ├── models/deletion.py           # W3 deletion target constraint/state
│   ├── repo/                        # column-minimal Authority and deletion queries
│   ├── runtime/
│   │   ├── w3_authority_adapter.py  # isolated private W1 FastAPI boundary
│   │   ├── w3_deletion_worker.py    # durable W3 deletion transport/ack adapter
│   │   └── w3_runtime_preflight.py  # identity/queue/Authority/runtime checks
│   └── services/                    # currentness and deletion transaction rules
├── infra/
│   ├── w1-runtime.compose.yml       # W1 receive worker plus private Authority profile
│   ├── w3-runtime.compose.yml       # pinned W3 CLI services and shared state
│   ├── aws/                         # send-only/receive-only policy templates
│   └── postgres/                    # minimum roles/column grants
├── migrations/versions/             # W3 deletion target/grant migrations
├── scripts/                         # preflight, scheduler entrypoints, CT-12 harness/evidence
└── tests/
    ├── contract/
    ├── integration/db/
    └── runtime/

w3/
├── Dockerfile                       # locked non-root image recipe owned by W1 deployment
├── pyproject.toml
├── uv.lock
└── src/w3_knowledge/                # pinned W3 source; changes require W3-owned SHA
```

**Structure Decision**: Keep W1 authoritative adapters and PostgreSQL changes inside `backend/`.
Place only the deployment recipe beside the received W3 package; do not modify W3 domain code under
its pinned SHA as part of M1. If M2/M3 require W3 code changes, they arrive as a new W3-owned full
SHA before adoption. AWS templates contain placeholders, never actual account secrets or identifiers.

## Gate Register

| Gate | Missing input | Blocks | Owner |
| --- | --- | --- | --- |
| W3-A | actual AnalysisPlan caller repository/SHA/function/semantics/idempotency | M2, M4, M5 | analysis pipeline owner / integration coordinator |
| W3-B | Authority adapter owner, revision milestone, timeout/error mapping acceptance | M2, M4, M5 | W3 runtime owner |
| W3-C | delete-owner adapter/ack/retry mapping | M3, M5 | W3 runtime owner |
| W3-D | image/entrypoint/state-layout review | final M1 closure, then M4/M5 | W3 runtime owner |
| W3-E | runtime state/retention/replay/backup policy confirmation | M3, M5 | W3 runtime owner + product/privacy approver |
| W3-F | actual supplier/relay joint executor and evidence location | M5 | W3/analysis runtime owner |

## Complexity Tracking

No constitution violation is required. The second database engine is not W1 application storage:
it is W3's already-adopted single-host delivery ledger. W1 authoritative state remains PostgreSQL,
and cross-store atomicity is handled through durable, idempotent dispatch rather than a distributed
transaction or direct SQLite mutation from W1.
