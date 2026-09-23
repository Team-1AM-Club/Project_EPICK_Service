"""Release-manifest schema and deliberately unfilled template checks."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = (
    ROOT / "backend" / "contracts" / "integration" / "v1" / "deployment-manifest.schema.json"
)
TEMPLATE_PATH = ROOT / "backend" / "infra" / "deployment-manifest.example.json"
LOCAL_ENV_TEMPLATE_PATH = ROOT / "backend" / "infra" / "full-service-local.env.example"
VALIDATOR = Draft202012Validator(
    json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
    format_checker=FormatChecker(),
)


def _valid_manifest() -> dict[str, object]:
    components = []
    for name in ("frontend", "w1", "w2", "w3", "w4"):
        components.append(
            {
                "name": name,
                "repository": f"epick-{name}",
                "source_sha": "a" * 40,
                "lock_hash": "b" * 64,
                "contract_hashes": ["c" * 64],
                "artifact": {
                    "kind": "vercel-deployment" if name == "frontend" else "oci-image",
                    "immutable_id": "sha256:" + "d" * 64,
                },
                "entrypoints": ["service"],
                "health_probe": "/health/ready",
                "rollback_artifact": "sha256:" + "e" * 64,
            }
        )
    return {
        "schema_version": "epick.deployment/1.0",
        "release_id": "local-integration-001",
        "environment": "local-e2e",
        "status": "DRAFT",
        "policy_manifest_revision": "reviewed-local-policy",
        "components": components,
        "created_at": "2026-09-21T00:00:00Z",
    }


def test_manifest_schema_accepts_pinned_five_component_draft() -> None:
    assert list(VALIDATOR.iter_errors(_valid_manifest())) == []


def test_manifest_schema_rejects_unpinned_source_and_secret_field() -> None:
    manifest = _valid_manifest()
    components = manifest["components"]
    assert isinstance(components, list)
    components[0]["source_sha"] = "staging"
    components[1]["database_password"] = "forbidden"

    errors = list(VALIDATOR.iter_errors(manifest))
    assert {tuple(error.path) for error in errors} == {
        ("components", 0, "source_sha"),
        ("components", 1),
    }


def test_names_only_template_cannot_be_mistaken_for_deployable_manifest() -> None:
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    assert template["status"] == "DRAFT"
    assert {component["name"] for component in template["components"]} == {
        "frontend",
        "w1",
        "w2",
        "w3",
        "w4",
    }
    assert list(VALIDATOR.iter_errors(template))
    serialized = json.dumps(template).lower()
    assert "arn:aws:" not in serialized
    assert "access_key" not in serialized
    assert "secret_key" not in serialized
    assert "password" not in serialized
    assert "bearer " not in serialized


def test_local_environment_template_contains_placeholders_and_file_references_only() -> None:
    document = LOCAL_ENV_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "INTEGRATION_ENABLED=true" in document
    assert "INTEGRATION_REAL_DATA_ENABLED=false" in document
    assert "DEPLOYMENT_MANIFEST_FILE=SET_LOCAL_FILE" in document
    assert "W2_RUNTIME_ENV_FILE=SET_LOCAL_FILE" in document
    assert "W3_RUNTIME_ENV_FILE=SET_LOCAL_FILE" in document
    assert "W4_RUNTIME_ENV_FILE=SET_LOCAL_FILE" in document
    lowered = document.lower()
    for forbidden in (
        "arn:aws:",
        "https://",
        "http://",
        "postgresql://",
        "password=",
        "secret=",
        "token=",
        "bearer=",
        "access_key=",
    ):
        assert forbidden not in lowered
