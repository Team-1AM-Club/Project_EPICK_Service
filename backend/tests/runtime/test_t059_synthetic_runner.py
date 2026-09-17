from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import configure_mappers

from app.models.deletion import DeletionRequest  # noqa: F401
from app.models.identity import User  # noqa: F401
from app.models.jobs import Job, OutboxMessage  # noqa: F401


def test_t059_synthetic_runner_registers_outbox_foreign_key_models() -> None:
    """The standalone runner must configure its ORM graph before it seeds Jobs."""

    runner_path = Path(__file__).resolve().parents[2] / "scripts" / "run_w1_t059_synthetic.py"
    assert "from app.models.deletion import DeletionRequest" in runner_path.read_text(
        encoding="utf-8"
    )

    configure_mappers()
