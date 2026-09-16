# Project EPICK Service — W3 Knowledge Validation

Current C01 canonical: [W3 contract reply, profile r2](docs/w3-additional-reply-2026-09-16.md).
The adjacent SHA256 sidecar identifies its exact bytes. This remains a candidate,
not a jointly adopted contract. Earlier handoffs are historical reference only.
The confirmed scope is version-specific, with null restrictions applying to the whole Source.

The W3 service validates SourceVersion and Evidence lineage, structures Claims and
Requirements, and projects only verified, usable knowledge to W4.

## Local verification

```powershell
uv run --locked pytest -q
uv run --locked ruff check src/w3_knowledge tests
uv run --locked ruff format --check src/w3_knowledge tests
```

Solar live validation is intentionally opt-in and requires explicit
`--run-live` authorization and provider configuration.

The implementation specification and verification record are in
[`specs/001-source-knowledge-validation`](specs/001-source-knowledge-validation/).

## Offline event/index validation lab

Run synthetic restriction, index mismatch, replay/snapshot recovery, and retention
scenarios with Python 3.11+ (no third-party packages):

```powershell
python src/w3_knowledge/lab.py --scenario examples/w3-lab-scenario.json
```

See the [handoff and limitations](docs/w3-validation-lab.md). This standalone lab
does not implement the official event/ACK contract or connect to production services.

## Restriction integration candidate

The executable `w3-restriction/0.1-draft` candidate now provides an authenticated
local HTTP consumer, persistent SQLite FTS search, replay/snapshot recovery,
retention enforcement and a durable W4 signal/cache adapter.

```powershell
uv sync --locked
uv run --locked python scripts/restriction_smoke.py
```

This starts a real local server process and writes the results under `.runtime/`.
Read the [PM response and runbook](docs/w3-restriction-handoff.md) and
[proposed contract](contracts/restriction/v0.1-draft/README.md).
Team adoption and actual W2/W4 application deployment remain pending.

## W2 C-01 compatible candidate

The separate `w3-c01/0.2-candidate` accepts the supplied W2 envelope/payload and UUIDs,
tracks transport and restriction revisions separately, and provides recovery,
Evidence-bound indexing and a W4 reference cache. Use a new database and explicit
restriction scope and TTL settings; this is not an in-place legacy migration.

```powershell
uv sync --locked
uv run --locked python scripts/c01_smoke.py
```

See the [C01 implementation handoff](docs/w3-c01-handoff-2026-09-16.md) for exact
schemas, test evidence, endpoints, and decisions still requiring W2/W4 agreement.

The [additional W2 reply and W4 agreement request](docs/w3-additional-reply-2026-09-16.md)
adds the knowledge DTO, typed reference consumer, ACK redelivery tests, and W1 private
deletion proposal. W4 team acceptance and actual service integration remain pending.
