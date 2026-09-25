import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).parents[2]


def test_c01_dockerfile_is_locked_non_root_and_health_checked():
    value = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert "uv sync --frozen --no-dev" in value
    assert "USER w3" in value
    assert "HEALTHCHECK" in value and "/health" in value
    assert 'ENTRYPOINT ["/app/.venv/bin/python", "-m", "w3_knowledge.c01.http"]' in value
    assert "COPY uv.lock pyproject.toml" in value


def test_c01_compose_keeps_code_immutable_state_durable_and_network_private():
    value = (ROOT / "deploy" / "c01.compose.yml").read_text(encoding="utf-8")
    for required in (
        "read_only: true",
        "no-new-privileges:true",
        "cap_drop:",
        "- ALL",
        "internal: true",
        "c01-data:/var/lib/w3",
        'restart: "on-failure:3"',
        "--host",
        "0.0.0.0",
        "W3_W2_TOKEN",
        "W3_OPERATOR_TOKEN",
        "W3_W4_TOKEN",
        "W3_SOURCE_AUTHORITY_ENDPOINT",
        "W3_SOURCE_AUTHORITY_TOKEN",
    ):
        assert required in value
    assert "ports:" not in value


def test_phase4_runbook_covers_each_required_operator_path_without_secret_values():
    value = (ROOT / "docs" / "w3-c01-phase4-runbook-2026-09-25.md").read_text(encoding="utf-8")
    for required in (
        "GET /health",
        "GET /c01/v1/status/{source_id}",
        "POST /c01/v1/replay",
        "POST /c01/v1/snapshot",
        "POST /c01/v1/index",
        "POST /c01/v1/purge",
        "w3_knowledge.c01.operator backup",
        "w3_knowledge.c01.operator restore",
        "W3_SOURCE_AUTHORITY_TOTAL_TIMEOUT_SECONDS",
        "W3_C01_MAX_TTL_SECONDS",
    ):
        assert required in value
    assert "actual-token" not in value


def test_t058_count_evidence_example_is_schema_valid_and_anonymous():
    schema_path = ROOT / "contracts" / "c01" / "v0.2-candidate" / "t058-count-evidence.schema.json"
    example_path = schema_path.parent / "examples" / "t058-count-evidence.json"

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    example = json.loads(example_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(example)

    serialized = json.dumps(example, sort_keys=True).lower()
    for prohibited in (
        "source_id",
        "version_id",
        "owner",
        "bearer",
        "raw_body",
        "database_url",
    ):
        assert prohibited not in serialized
