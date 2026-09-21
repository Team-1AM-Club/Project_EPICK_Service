from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.models.w2_commit_operations import W2StagedResult

pytestmark = pytest.mark.postgres

BACKEND_ROOT = Path(__file__).parents[3]
RUNTIME_ROLE_TEMPLATE_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_roles.sql"
RUNTIME_PRIVILEGES_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_privileges.sql"


def test_staged_result_schema_has_lifecycle_constraints_and_force_rls(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)
    assert "w2_staged_results" in inspector.get_table_names()
    assert {
        "operation_id",
        "command_id",
        "owner_user_id",
        "origin_message_id",
        "schema_version",
        "producer_name",
        "occurred_at",
        "payload_digest",
        "result_digest",
        "result_payload",
        "payload_state",
        "consumed_at",
        "cleared_at",
    } <= {column["name"] for column in inspector.get_columns("w2_staged_results")}
    assert any(
        constraint["column_names"] == ["command_id"]
        for constraint in inspector.get_unique_constraints("w2_staged_results")
    )
    checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("w2_staged_results")
    }
    for fragment in (
        "payload_digest_sha256",
        "result_digest_sha256",
        "payload_state_allowed",
        "payload_lifec",
    ):
        assert any(fragment in constraint_name for constraint_name in checks)
    with migrated_engine.connect() as connection:
        rls_state = connection.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'public.w2_staged_results'::regclass"
            )
        ).one()
        policies = set(
            connection.scalars(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = 'public' AND tablename = 'w2_staged_results'"
                )
            )
        )
    assert rls_state == (True, True)
    assert {
        "w2_staged_results_owner_policy",
        "w2_staged_results_worker_operational_policy",
        "w2_staged_results_deleter_operational_policy",
    } <= policies


def test_staged_result_rejects_invalid_lifecycle_or_digest(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        owner_id = uuid4()
        job_id = uuid4()
        command_id = uuid4()
        operation_id = uuid4()
        connection.execute(
            text("SELECT set_config('app.current_user_id', :owner_id, true)"),
            {"owner_id": str(owner_id)},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, locale, timezone) "
                "VALUES (:id, 'W2 staged owner', 'ko-KR', 'Asia/Seoul')"
            ),
            {"id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (id, owner_user_id, job_type, owner_deletion_epoch) "
                "VALUES (:id, :owner_id, 'SOURCE_COLLECTION', 0)"
            ),
            {"id": job_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO job_commands (id, job_id, owner_user_id, command_type, "
                "command_schema_version, command_sequence, execution_fence, "
                "owner_deletion_epoch, payload) VALUES (:id, :job_id, :owner_id, "
                "'W2_SOURCE_COLLECTION', '1.0', 1, 1, 0, '{}'::jsonb)"
            ),
            {"id": command_id, "job_id": job_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO w2_commit_operations (id, command_id, job_id, owner_user_id, "
                "execution_fence, owner_deletion_epoch, result_digest) VALUES "
                "(:id, :command_id, :job_id, :owner_id, 1, 0, :digest)"
            ),
            {
                "id": operation_id,
                "command_id": command_id,
                "job_id": job_id,
                "owner_id": owner_id,
                "digest": "sha256:" + "a" * 64,
            },
        )
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO w2_staged_results (operation_id, command_id, owner_user_id, "
                        "origin_message_id, schema_version, producer_name, occurred_at, "
                        "payload_digest, result_digest, result_payload, payload_state) VALUES "
                        "(:operation_id, :command_id, :owner_id, :message_id, 'w2.private', 'w2', "
                        "now(), 'not-a-digest', :result_digest, '{}'::jsonb, 'ACTIVE')"
                    ),
                    {
                        "operation_id": operation_id,
                        "command_id": command_id,
                        "owner_id": owner_id,
                        "message_id": uuid4(),
                        "result_digest": "sha256:" + "a" * 64,
                    },
                )


def test_runtime_roles_receive_only_required_staged_result_dml(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")))
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))
    with migrated_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT has_table_privilege('epick_worker', "
                "'public.w2_staged_results', 'INSERT, SELECT, UPDATE')"
            )
        )
        assert connection.scalar(
            text(
                "SELECT has_table_privilege('epick_deleter', "
                "'public.w2_staged_results', 'INSERT, SELECT, UPDATE')"
            )
        )
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege("
                "'epick_runtime', 'public.w2_staged_results', 'UPDATE')"
            )
        )


def test_revision_027_upgrades_from_026_to_head(
    fresh_migration_config: Config,
) -> None:
    command.upgrade(fresh_migration_config, "026_w3_core_decision_inbound")
    command.upgrade(fresh_migration_config, "027_w2_staged_result_adoption")
    command.upgrade(fresh_migration_config, "head")
    assert W2StagedResult.__table__.name == "w2_staged_results"
