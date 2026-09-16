# Restriction Runtime Implementation Plan

> Execute inline with superpowers:executing-plans, one tested component at a time.

**Goal:** Deliver the PM contract candidate and executable W3 integration boundary.
**Architecture:** Strict DTOs → authenticated HTTP → durable state/FTS/outbox → W4 adapter.
**Tech Stack:** Python >=3.12, Pydantic 2, stdlib HTTP, SQLite FTS5, pytest.
**Spec:** ../specs/2026-09-13-restriction-runtime-design.md

## Global constraints

- Contract version `w3-restriction/0.1-draft`; team approval is not inferred.
- No raw bodies/private identifiers/secrets in public events or signals.
- Existing knowledge and lab behavior remains independently testable.
- Localhost only, required role credentials, no new external service dependencies.

## Tasks

- [x] R01: `restriction/contracts.py`, generated `contracts/restriction/v0.1-draft/`:
  strict event/snapshot/replay/index/signal DTOs, valid/invalid fixtures. Test rejects
  unknown fields/future versions/type coercion and inconsistent replay sources.
- [x] R02: `restriction/store.py`, `tests/integration/test_restriction_runtime.py`:
  durable consume/status/replay/snapshot/index/search/purge/outbox. Test actual
  restriction invalidation, restart/dedup, conflict/gap, stale recovery, FTS mismatch
  and SQL write failure; assert no success ACK or text leak during blocked states.
- [x] R03: `restriction/http.py`, `restriction/w4.py`, `restriction/guard.py`:
  real HTTP entrypoint, scoped tokens, durable monotonic W4 cache gate, existing
  knowledge retained-text guard. Test HTTP auth, durable outbox receipt and stale
  cache rejection after restriction/index/retention changes.
- [x] R04: `scripts/restriction_smoke.py`, `.env.example`, handoff/runbook and CI:
  start a fresh service, run fixture events through real HTTP and FTS, record
  reproducible results. Document role ownership, retries, retention boundaries,
  contract adoption gates and remaining W2/W4 application integration explicitly.

For each task run its pytest target before implementation (expected missing symbol
or missing behavior failure), implement, then rerun until passing. Final verification:
`pytest -q`, `ruff check src tests scripts`, `ruff format --check src tests scripts`,
and the packaged real-HTTP smoke command. Record actual results in the handoff.

## Completion boundary

R01–R04 implement the local integration candidate. Added review regressions for
cross-Source artifacts, integer overflow, concurrent DB connections and full SQL
rollback. Team contract adoption, W2 producer wiring, existing W4 application
wiring, W1 policy and shared-environment deployment remain open external steps.
Their absence is recorded in the handoff; they are not marked complete here.
