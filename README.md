# Project EPICK Service — W3 Knowledge Validation

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
