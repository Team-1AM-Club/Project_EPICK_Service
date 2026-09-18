from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

BACKEND_ROOT = Path(__file__).parents[3]
RUNTIME_ROLE_TEMPLATE_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_roles.sql"
RUNTIME_PRIVILEGES_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_privileges.sql"


def _seed_private_command(migrated_engine: Engine) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    owner_id = uuid4()
    job_id = uuid4()
    command_id = uuid4()
    outbox_id = uuid4()
    operation_id = uuid4()
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")))
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))
        connection.execute(
            text("SELECT set_config('app.current_user_id', :owner_id, true)"),
            {"owner_id": str(owner_id)},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, locale, timezone) "
                "VALUES (:id, 'Runtime access owner', 'ko-KR', 'Asia/Seoul')"
            ),
            {"id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO jobs ("
                "id, owner_user_id, job_type, owner_deletion_epoch"
                ") VALUES (:id, :owner_id, 'COLLECT_COMPANY_SOURCE', 0)"
            ),
            {"id": job_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO job_commands ("
                "id, job_id, owner_user_id, command_type, command_schema_version, "
                "command_sequence, execution_fence, owner_deletion_epoch, payload"
                ") VALUES ("
                ":id, :job_id, :owner_id, 'EXECUTE_JOB', '1.0', 1, 1, 0, "
                "CAST(:payload AS jsonb)"
                ")"
            ),
            {
                "id": command_id,
                "job_id": job_id,
                "owner_id": owner_id,
                "payload": json.dumps({"command_type": "EXECUTE_JOB"}),
            },
        )
        connection.execute(
            text(
                "INSERT INTO outbox_messages ("
                "id, message_type, schema_version, visibility_scope, aggregate_type, "
                "aggregate_id, aggregate_revision, command_id, job_id, owner_user_id, "
                "execution_fence, owner_deletion_epoch, payload"
                ") VALUES ("
                ":id, 'job.command.dispatch', '1.0', 'PRIVATE', 'JOB', "
                ":job_id, 1, :command_id, :job_id, :owner_id, 1, 0, "
                "CAST(:payload AS jsonb)"
                ")"
            ),
            {
                "id": outbox_id,
                "command_id": command_id,
                "job_id": job_id,
                "owner_id": owner_id,
                "payload": json.dumps(
                    {
                        "command_id": str(command_id),
                        "job_id": str(job_id),
                        "execution_fence": 1,
                        "owner_deletion_epoch": 0,
                    }
                ),
            },
        )
        connection.execute(
            text(
                "INSERT INTO w2_commit_operations ("
                "id, command_id, job_id, owner_user_id, execution_fence, "
                "owner_deletion_epoch, result_digest, operation_revision, state"
                ") VALUES ("
                ":id, :command_id, :job_id, :owner_id, 1, 0, :result_digest, 1, "
                "'PREPARE_PENDING'"
                ")"
            ),
            {
                "id": operation_id,
                "command_id": command_id,
                "job_id": job_id,
                "owner_id": owner_id,
                "result_digest": "sha256:" + "a" * 64,
            },
        )
    return owner_id, job_id, command_id, outbox_id, operation_id


@pytest.mark.postgres
def test_worker_and_lookup_roles_receive_only_their_operational_rls_access(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id, outbox_id, operation_id = _seed_private_command(
        migrated_engine
    )

    with migrated_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET LOCAL ROLE epick_runtime"))
            assert connection.scalar(text("SELECT count(*) FROM jobs")) == 0
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM w2_commit_operations WHERE id = :operation_id"),
                    {"operation_id": operation_id},
                )
                == 1
            )

    with migrated_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET LOCAL ROLE epick_lookup"))
            command = connection.execute(
                text(
                    "SELECT id, execution_fence, owner_deletion_epoch, payload "
                    "FROM job_commands WHERE id = :command_id"
                ),
                {"command_id": command_id},
            ).mappings().one()
            assert command["id"] == command_id
            assert command["execution_fence"] == 1
            assert command["owner_deletion_epoch"] == 0
            assert command["payload"] == {"command_type": "EXECUTE_JOB"}

            with pytest.raises(DBAPIError):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE jobs SET status = 'RUNNING' WHERE id = :job_id"),
                        {"job_id": job_id},
                    )
            with pytest.raises(DBAPIError):
                with connection.begin_nested():
                    connection.execute(text("SELECT id FROM outbox_messages"))
            with pytest.raises(DBAPIError):
                with connection.begin_nested():
                    connection.execute(text("SELECT id FROM w2_commit_operations"))

    with migrated_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET LOCAL ROLE epick_worker"))
            assert connection.scalar(
                text("SELECT id FROM users WHERE id = :owner_id FOR UPDATE"),
                {"owner_id": owner_id},
            ) == owner_id
            with pytest.raises(DBAPIError):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE users SET updated_at = now() WHERE id = :owner_id"),
                        {"owner_id": owner_id},
                    )
            connection.execute(
                text(
                    "UPDATE outbox_messages SET "
                    "status = 'PUBLISHING', relay_claim_token = :claim_token, "
                    "relay_claimed_by = 'test-relay', "
                    "relay_lease_expires_at = now() + interval '120 seconds' "
                    "WHERE id = :outbox_id"
                ),
                {"claim_token": uuid4(), "outbox_id": outbox_id},
            )
            status = connection.scalar(
                text("SELECT status FROM outbox_messages WHERE id = :outbox_id"),
                {"outbox_id": outbox_id},
            )
            assert status == "PUBLISHING"
            connection.execute(
                text(
                    "UPDATE w2_commit_operations SET state = 'PREPARED', "
                    "operation_revision = 2 WHERE id = :operation_id"
                ),
                {"operation_id": operation_id},
            )
            assert connection.scalar(
                text("SELECT state FROM w2_commit_operations WHERE id = :operation_id"),
                {"operation_id": operation_id},
            ) == "PREPARED"
            # The migration history test intentionally downgrades below the revision
            # that introduced PUBLISHING.  Do not leave this fixture row in a state
            # that could not have existed at that historical revision.
            connection.execute(
                text(
                    "UPDATE outbox_messages SET "
                    "status = 'PUBLISHED', published_at = now(), "
                    "relay_claim_token = NULL, relay_claimed_by = NULL, "
                    "relay_lease_expires_at = NULL "
                    "WHERE id = :outbox_id"
                ),
                {"outbox_id": outbox_id},
            )


@pytest.mark.postgres
def test_worker_can_read_and_insert_but_not_mutate_core_decision_bindings(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")))
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))

    with migrated_engine.connect() as connection:
        with connection.begin():
            connection.execute(text("SET LOCAL ROLE epick_worker"))
            assert connection.scalar(
                text("SELECT count(*) FROM job_core_decision_bindings")
            ) == 0
            with pytest.raises(DBAPIError):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE job_core_decision_bindings "
                            "SET decision_version = decision_version + 1"
                        )
                    )
            with pytest.raises(DBAPIError):
                with connection.begin_nested():
                    connection.execute(text("DELETE FROM job_core_decision_bindings"))
