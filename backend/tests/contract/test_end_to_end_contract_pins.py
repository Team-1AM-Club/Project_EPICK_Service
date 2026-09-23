"""W1-side pins for the three new integration contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
DESIGN_CONTRACTS = ROOT / "specs" / "008-end-to-end-service" / "contracts"
PUBLIC_CONTRACTS = ROOT / "backend" / "contracts" / "integration" / "v1"
PINS = {
    "public-collection-api.openapi.yaml": (
        "13107b34c8171c72c39324267b09a7d072a35fb29b38bc2adcb1f0f0e534be94"
    ),
    "w1-w3-analysis-plan-command.schema.json": (
        "136d0fbe9a4f1978f658c7f3fa37f549269431047eb1917bf699c9bcae802145"
    ),
    "deployment-manifest.schema.json": (
        "148f1c854cc19e5e06d443ef32771f9cd92d4bca9fd85469b42034a8eb0cffe4"
    ),
}


def _sha256(path: Path) -> str:
    canonical = path.read_bytes().replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"
    return hashlib.sha256(canonical).hexdigest()


@pytest.mark.parametrize(("name", "expected"), PINS.items())
def test_public_integration_copy_matches_design_pin(name: str, expected: str) -> None:
    assert _sha256(PUBLIC_CONTRACTS / name) == expected
    # Design docs are ignored in some CI checkouts; the versioned Service copy
    # remains mandatory, while local design drift is checked when available.
    design_path = DESIGN_CONTRACTS / name
    if design_path.is_file():
        assert _sha256(design_path) == expected


def test_collection_openapi_keeps_owner_scoped_idempotent_start() -> None:
    document = (PUBLIC_CONTRACTS / "public-collection-api.openapi.yaml").read_text(
        encoding="utf-8"
    )
    assert "openapi: 3.1.0" in document
    assert "/api/v1/application-projects/{project_id}/source-collections:" in document
    assert "name: Idempotency-Key" in document
    assert "'202':" in document


@pytest.mark.parametrize(
    "name",
    ("w1-w3-analysis-plan-command.schema.json", "deployment-manifest.schema.json"),
)
def test_versioned_json_schemas_are_valid_draft_2020_12(name: str) -> None:
    schema = json.loads((PUBLIC_CONTRACTS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
