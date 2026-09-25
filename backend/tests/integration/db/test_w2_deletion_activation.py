"""W1 activation fails closed unless a recent W2 DB-head check is bound to pins."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from app.runtime import integration_preflight

_SOURCE_SHA = "e2491a4084ed50090d5135ba177a3772e9a44f5a"
_IMAGE_DIGEST = "sha256:" + "a" * 64
_COMMAND_HASH = "73eb3a51d15923969d483b13fdbf498e0a6cd8cfa18c019a66f5f532cfd64067"
_ACK_HASH = "22cd9cd44061b5c14c9852634417482993a8ffb8f69dc8e98e3b6cd71b891bc9"
_BACKEND_ROOT = Path(__file__).parents[3]


@pytest.fixture
def w2_probe_schema(migrated_engine: Engine) -> Iterator[str]:
    schema = f"w2_activation_{uuid4().hex}"
    with migrated_engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    try:
        yield schema
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))


def test_activation_operator_has_a_runnable_preflight_entrypoint() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(_BACKEND_ROOT / "scripts" / "preflight_w2_deletion_activation.py"),
            "--help",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--output" in result.stdout


@pytest.mark.postgres
def test_activation_operator_writes_proof_only_after_real_w2_head_check(
    migrated_engine: Engine, tmp_path: Path, w2_probe_schema: str
) -> None:
    schema = w2_probe_schema
    with migrated_engine.begin() as connection:
        connection.execute(text(f"CREATE TABLE {schema}.alembic_version (version_num varchar(64))"))
        connection.execute(
            text(
                f"INSERT INTO {schema}.alembic_version (version_num) "
                "VALUES ('0010_private_deletion_scope_v2')"
            )
        )
    w2_url = migrated_engine.url.update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    environment = {
        **os.environ,
        "W2_DELETION_PREFLIGHT_DATABASE_URL": w2_url,
        "W2_DELETION_SOURCE_SHA": _SOURCE_SHA,
        "W2_DELETION_IMAGE_DIGEST": _IMAGE_DIGEST,
    }
    command = [
        sys.executable,
        str(_BACKEND_ROOT / "scripts" / "preflight_w2_deletion_activation.py"),
        "--output",
    ]
    proof_path = tmp_path / "w2-activation.json"
    passed = subprocess.run(
        [*command, str(proof_path)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert passed.returncode == 0, passed.stderr
    assert json.loads(passed.stdout) == {
        "status": "PREFLIGHT_PASSED",
        "scope": "w2_deletion_v2",
    }
    assert json.loads(proof_path.read_text(encoding="utf-8"))["w2_migration_head"] == (
        "0010_private_deletion_scope_v2"
    )

    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                f"UPDATE {schema}.alembic_version SET version_num = '0009_private_deletion_receipt'"
            )
        )
    rejected_path = tmp_path / "rejected.json"
    rejected = subprocess.run(
        [*command, str(rejected_path)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "W2_DELETION_ACTIVATION_PREFLIGHT_FAILED" in rejected.stderr
    assert w2_url not in rejected.stderr
    assert not rejected_path.exists()


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("heads", "allowed"),
    [
        (("0010_private_deletion_scope_v2",), True),
        ((), False),
        (("0009_private_deletion_receipt",), False),
        (("0010_private_deletion_scope_v2", "0010_private_deletion_scope_v2"), False),
    ],
)
def test_w2_deletion_activation_requires_exact_one_actual_database_head(
    migrated_engine: Engine, heads: tuple[str, ...], allowed: bool
) -> None:
    # This temporary table shadows W1's public alembic_version only for this
    # connection. The verifier therefore reads real PostgreSQL rows without
    # mutating either W1 or W2's persistent migration state.
    make_proof = getattr(integration_preflight, "create_w2_deletion_activation_proof", None)
    assert callable(make_proof)
    with migrated_engine.begin() as connection:
        connection.execute(
            text("CREATE TEMP TABLE alembic_version (version_num varchar(64)) ON COMMIT DROP")
        )
        for head in heads:
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:head)"), {"head": head}
            )
        if not allowed:
            with pytest.raises(integration_preflight.IntegrationPreflightError):
                make_proof(
                    connection,
                    observed_source_sha=_SOURCE_SHA,
                    image_digest=_IMAGE_DIGEST,
                    checked_at=datetime(2026, 9, 25, 0, 0, tzinfo=UTC),
                )
        else:
            proof = make_proof(
                connection,
                observed_source_sha=_SOURCE_SHA,
                image_digest=_IMAGE_DIGEST,
                checked_at=datetime(2026, 9, 25, 0, 0, tzinfo=UTC),
            )
            assert proof == {
                "schema_version": "w1.w2-deletion-activation.v1",
                "w2_source_sha": _SOURCE_SHA,
                "w2_migration_head": "0010_private_deletion_scope_v2",
                "w2_image_digest": _IMAGE_DIGEST,
                "command_schema_sha256": _COMMAND_HASH,
                "ack_schema_sha256": _ACK_HASH,
                "checked_at": "2026-09-25T00:00:00Z",
            }


def test_w2_deletion_activation_rejects_stale_or_changed_proof(tmp_path: Path) -> None:
    require_proof = getattr(integration_preflight, "require_w2_deletion_activation_proof", None)
    assert callable(require_proof)
    now = datetime(2026, 9, 25, 0, 5, tzinfo=UTC)
    proof = {
        "schema_version": "w1.w2-deletion-activation.v1",
        "w2_source_sha": _SOURCE_SHA,
        "w2_migration_head": "0010_private_deletion_scope_v2",
        "w2_image_digest": _IMAGE_DIGEST,
        "command_schema_sha256": _COMMAND_HASH,
        "ack_schema_sha256": _ACK_HASH,
        "checked_at": "2026-09-25T00:00:00Z",
    }
    path = tmp_path / "activation.json"
    path.write_text(json.dumps(proof), encoding="utf-8")
    assert require_proof(path, expected_image_digest=_IMAGE_DIGEST, now=now) == proof
    for change in (
        {"w2_migration_head": "0009_private_deletion_receipt"},
        {"w2_source_sha": "0" * 40},
        {"w2_image_digest": "sha256:" + "b" * 64},
        {"ack_schema_sha256": "0" * 64},
        {"checked_at": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")},
        {"checked_at": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")},
    ):
        path.write_text(json.dumps({**proof, **change}), encoding="utf-8")
        with pytest.raises(integration_preflight.IntegrationPreflightError):
            require_proof(path, expected_image_digest=_IMAGE_DIGEST, now=now)
