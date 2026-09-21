# Implementation Plan: W1–W3 Actual Runtime Integration

**Branch**: `007-w1-w3-runtime-integration` | **Date**: 2026-09-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/007-w1-w3-runtime-integration/spec.md`

## Summary

Re-baseline the locally verified W3 Core runtime at implementation SHA
`66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b` from the independent clone at
`w3/Project_EPICK_Service` and connect it to W1's authoritative Job/deletion/Source state without
changing the adopted W3 event wire. The clone is currently at documentation receipt SHA
`bad8671b2ea8d02bdce2157120b94d2b7edf09d8`; the implementation SHA is its ancestor and no
runtime source changed between the two commits. M1 rebuilds and verifies the non-root/read-only W3
image plus durable `/state/core.db`. M2 implements the W1 Authority boundary now, then binds the
actual AnalysisPlan caller and W3 Authority client after W3-A/B. M3 connects durable owner deletion
and permanent Source-retirement dispatch to the approved `w3.retention/1.1` runtime and deploys
relay/expire/inspect/backup/HELD operations. M4 binds an actual send-only W3 IAM role to the existing
W1 receive-only SQS boundary. M5 runs actual-producer joint CT-12 and records count reconciliation
and teardown. W1-owned work in M1, M2 and M3 may proceed in parallel; actual caller cutover, final
dispatcher adoption, IAM binding and joint execution retain explicit gates.

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
raw SQLite/volume/snapshot restore is unsupported; runtime retention values are fixed by
`w3.retention/1.1`; actual caller, private adapter transport, workload identity and joint-window
gates remain explicit

**Scale/Scope**: One actual W3 runtime deployment, one W1 private Authority adapter, durable owner
deletion and permanently retired Source dispatch, one actual send-only IAM role, one Main/DLQ
binding, and one joint CT-12 evidence run; W2, W4 and Neo4j work remain out of scope

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
- **Gated values remain gated — PASS**: `w3.retention/1.1` is adopted from the W3 implementation
  rather than redefined by W1. Actual caller semantics, private adapter transport acceptance, W3
  workload identity and joint window are not fabricated. W1-owned M1/M2/M3 work is executable;
  gated cutover and completion claims remain blocked until their evidence exists.

### Post-design re-check

Phase 1 retains the same boundaries. The Authority contract returns only W3's existing
`Authorization` fields. Deletion is integrated through W1's existing PostgreSQL deletion workflow
instead of direct SQLite mutation from the API transaction. Runtime readiness and CT-12 evidence
contracts distinguish build/smoke proof from actual service completion. No gated production value
is assigned a placeholder that could be mistaken for READY.

## Design Decisions

1. Keep this work in a new feature rather than overwrite implemented inbound feature 003.
2. Treat `w3/Project_EPICK_Service` as a read-only upstream clone. Verify implementation commit
   `66a0e3e1...`, its ancestry to receipt HEAD `bad8671...`, and zero runtime-source drift; build an
   allowlisted archive of the implementation commit with its lockfile, publish by implementation
   tag and deploy by ECR manifest digest. Do not build from the mutable working-tree contents.
3. Run W3 as non-root with read-only rootfs, bounded `/tmp` tmpfs, and exactly one RW named volume
   mounted at `/state`; all commands use `/state/core.db`.
4. Use W1 PostgreSQL for authoritative currentness and deletion. W3 SQLite stores only W3 event,
   delivery, counter and deletion-suppression state.
5. Implement and test the minimal W1 Authority request/response boundary against the pinned W3
   `DecisionContext`/`Authorization` models now. Actual analysis caller binding and the W3 HTTP client
   adapter remain gated on W3-A/B; neither caller-supplied owner nor cached authority is trusted.
6. Extend W1's existing deletion target/outbox workflow with explicit W3 owner-deletion and permanent
   Source-retirement targets. The background adapters call only W3-owned `delete_owner` and
   `retire_source` interfaces through an approved private transport; API transactions never invoke
   Docker or write SQLite directly. Transient Source unavailability never becomes retirement.
7. Apply the immutable `w3.retention/1.1` values, including the required handoff retention value
   `1209600` seconds. Run relay/expire/inspect and redacted quarantine backup as bounded operations
   under W1-managed scheduling. `HELD` generates an alert and requires W3/operator review; it never
   causes automatic replay or extends the absolute body deadline.
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
| **M1 — immutable image·single-host state** | W3 implementation pin `66a0e3e1...` and clone are available; older image evidence is stale | Re-baseline provenance, create W1-owned immutable build recipe for the read-only clone, correct retention input, rebuild/retest, publish, and verify `/state` persistence | W3-D reviews entrypoint, shared SQLite, non-root/read-only layout before final digest | implementation and receipt SHAs + manifest digest; zero runtime drift; locked build; focused W3 tests; smoke; state persistence; W3-D disposition |
| **M2 — actual supplier·Authority** | W1 boundary work starts now; **actual cutover GATE: W3-A/B** | Add private Authority schema/service/read-only DB grants/HTTP adapter/preflight and consumer compatibility tests | actual analysis owner pins caller SHA/function/required-vs-optional semantics/idempotency; W3 client adapter pins auth/timeout/error mapping and supply/relay/replay recheck | valid/current succeeds; source/cancel/delete/epoch mismatch and 401/403/404/409/503/timeout fail closed; actual caller revision recorded |
| **M3 — deletion·Source retirement·operations** | `w3.retention/1.1` and W3-C implementation `66a0e3e1...` are verified; actual private Queue/Role binding verified on 2026-09-21 | Add owner-deletion and Source-retirement durable targets, dispatch routing, ack/failure reconciliation, bounded lifecycle consumer, scheduler, inspect/HELD/backup alarm and runbook | W3/W1 injected Queue/Role values outside Git and completed live duplicate/restart E2E | `evidence/m3-deletion-operations.json`: first apply 2, exact duplicate convergence 2, final pending delivery 0 |
| **M4 — actual IAM/SQS** | M1 and M2 complete; actual compute identity known | Create exact send-only role/policy, queue resource policy, W1 receive-only policy, root-only env and two-sided preflight | W3 workload proves default credential chain and stable role ID self-check | Role ARN/Role ID captured securely; W3 role ID = W1 expected SenderId; negative permissions denied |
| **M5 — joint CT-12** | M1–M4 + **GATE: W3-F** | Provision isolated DB/SQS window, run W1 consumer, capture DB/SQS counts and teardown | actual W3 caller/supplier/outbox/relay runs all scenarios and supplies inspect/digest evidence | CT12-01~12 pass; counts reconcile; no auto command; new fence exactly once; stale effects zero; cleanup manifest |

M1 re-baselining and W1-owned M2/M3 implementation may begin immediately. M1's source/recipe/smoke
can be completed before W3-D, but the final production-ready digest must not be declared closed until
W3-D is recorded. M2 actual-caller cutover, M3 dispatcher adoption, M4 IAM binding and M5 joint
execution stop at their stated gates; local contracts and synthetic tests do not close those gates.

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
    ├── source-retirement-command.schema.json
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
│   │   ├── w3_deletion_worker.py    # durable owner deletion transport/ack adapter
│   │   ├── w3_source_retirement_worker.py # permanent Source retirement adapter
│   │   └── w3_runtime_preflight.py  # identity/queue/Authority/runtime checks
│   └── services/                    # currentness and deletion transaction rules
├── infra/
│   ├── w3-runtime.Dockerfile        # W1-owned recipe; source comes from pinned Git archive
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

w3/Project_EPICK_Service/             # independent read-only W3 Git clone
├── .git/                             # proves implementation/receipt ancestry; never in image context
├── pyproject.toml
├── uv.lock
├── contracts/core-runtime/readiness.json
└── src/w3_knowledge/                 # W3-owned source; W1 does not patch it
```

**Structure Decision**: Keep W1 authoritative adapters, deployment recipe and PostgreSQL changes
inside `backend/`. Keep the independent W3 clone clean and build from an allowlisted archive of
`66a0e3e1...`; do not modify W3 domain code or send `.git`, `.venv` or working-tree-only files into
the Docker context. If M2/M3 require W3 code changes, they arrive as a new W3-owned full SHA before
adoption. AWS templates contain placeholders, never actual account secrets or identifiers.

## Re-baseline Record

| Item | Current value | Planning consequence |
| --- | --- | --- |
| W3 clone | `w3/Project_EPICK_Service`, branch `feat/w3-knowledge-validation` | Replaces the former unversioned `w3/` handoff copy |
| Runtime implementation | `66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b` | Immutable image and compatibility baseline |
| Clone receipt HEAD | `bad8671b2ea8d02bdce2157120b94d2b7edf09d8` | Documentation/readiness-only successor; record separately from image source |
| Runtime drift | none between implementation and receipt HEAD | W3 source can be built without waiting for another handoff |
| Focused W3 verification | `32 passed` | Baseline test evidence; rerun inside the image build workflow |
| Retention | `w3.retention/1.1` approved and implemented | W3-E policy-value gate is closed; scheduler/storage integration remains W1 work |
| Previous M1 image | `c7e678...` / local digest `sha256:441d...` | Historical only; must not be published or deployed as the current runtime |

## Gate Register

| Gate | Status | Remaining input | Blocks | Owner |
| --- | --- | --- | --- | --- |
| W3-A | OPEN | actual AnalysisPlan caller repository/SHA/function, trusted request time, required/optional Source semantics and idempotency | M2 cutover, M4, M5 | analysis pipeline owner / integration coordinator |
| W3-B | OPEN | actual Authority client adapter source pin, private auth, timeout and error/currentness mapping | M2 cutover, M4, M5 | W3 runtime owner + W1 integration owner |
| W3-C | VERIFIED | Dedicated Standard SQS command/receipt queues, stable Role ID authentication, APPLIED/DUPLICATE/STALE receipts, terminal no-receipt conflict, commit-before-receipt and pending-receipt-first restart are implemented at `66a0e3e1...`; T050 AWS E2E is complete | M5 | W3 runtime owner + W1 integration owner |
| W3-D | OPEN | updated `66a0e3e1...` image/entrypoint/state-layout review and manifest digest | final M1 closure, then M4/M5 | W3 runtime owner |
| W3-E | VERIFIED | `w3.retention/1.1` code, lifecycle semantics and policy values are pinned; deployment evidence is recorded by T050 | no implementation start | W3 runtime owner + product/privacy approver |
| W3-F | OPEN | actual supplier/relay joint executor, window and evidence location | M5 | W3/analysis runtime owner |

## Complexity Tracking

No constitution violation is required. The second database engine is not W1 application storage:
it is W3's already-adopted single-host delivery ledger. W1 authoritative state remains PostgreSQL,
and cross-store atomicity is handled through durable, idempotent dispatch rather than a distributed
transaction or direct SQLite mutation from W1.
