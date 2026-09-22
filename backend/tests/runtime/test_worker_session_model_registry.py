"""Every narrow worker session must have the complete ORM table registry."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.mark.parametrize(
    ("factory_name", "database_url_name"),
    [("create_worker_session_factory", "worker_database_url")],
)
def test_worker_session_resolves_all_foreign_keys_in_fresh_process(
    factory_name: str, database_url_name: str
) -> None:
    probe = dedent(
        """
        import sys
        from unittest.mock import patch

        from app.db.base import Base
        from app.models.jobs import OutboxMessage
        from app.runtime import session

        with patch.object(session.settings, sys.argv[2], "sqlite:///:memory:"):
            getattr(session, sys.argv[1])()

        assert OutboxMessage.__table__.name in Base.metadata.tables
        for table in Base.metadata.tables.values():
            for foreign_key in table.foreign_keys:
                foreign_key.column
        print("ORM_REGISTRY_OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, factory_name, database_url_name],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ORM_REGISTRY_OK"
