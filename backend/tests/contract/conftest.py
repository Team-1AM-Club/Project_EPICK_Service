from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="session")
def contract_root() -> Path:
    return BACKEND_ROOT / "contracts"


def load_contract_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value
