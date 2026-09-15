from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def contract_root() -> Path:
    return Path(__file__).parents[2] / "contracts"
