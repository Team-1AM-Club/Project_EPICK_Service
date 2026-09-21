from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parents[3]
BACKEND_CONTRACTS = REPO_ROOT / "backend" / "contracts"
FEATURE_CONTRACTS = REPO_ROOT / "specs" / "007-w1-w3-runtime-integration" / "contracts"
W3_CONTRACTS = REPO_ROOT / "w3" / "Project_EPICK_Service" / "contracts" / "core-runtime"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.skipif(not W3_CONTRACTS.is_dir(), reason="independent W3 clone is unavailable")
@pytest.mark.parametrize(
    ("backend_path", "feature_path", "w3_path"),
    [
        (
            "w1/v1/w3-owner-deletion.request.schema.json",
            "deletion-command.schema.json",
            "owner-deletion-command.schema.json",
        ),
        (
            "w1/v1/w3-source-retirement.request.schema.json",
            "source-retirement-command.schema.json",
            "source-retirement-command.schema.json",
        ),
        (
            "w3/v1/retention-receipt.schema.json",
            "lifecycle-receipt.schema.json",
            "lifecycle-receipt.schema.json",
        ),
    ],
)
def test_w1_adopts_the_exact_pinned_w3_lifecycle_schemas(
    backend_path: str,
    feature_path: str,
    w3_path: str,
) -> None:
    expected = _load(W3_CONTRACTS / w3_path)

    assert _load(BACKEND_CONTRACTS / backend_path) == expected
    assert _load(FEATURE_CONTRACTS / feature_path) == expected
