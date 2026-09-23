from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.models.lifecycle_operations import JobCheckpoint

pytestmark = pytest.mark.postgres


def _insert_checkpoint(
    engine: Engine,
    *,
    resume_stage: str,
    policy_revision: int | None,
) -> None:
    with engine.begin() as connection:
        owner_id = uuid4()
        job_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, locale, timezone) "
                "VALUES (:id, 'Phase 4 checkpoint owner', 'ko-KR', 'Asia/Seoul')"
            ),
            {"id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO jobs ("
                "id, owner_user_id, job_type, status, execution_fence, "
                "owner_deletion_epoch, analysis_input_version"
                ") VALUES ("
                ":id, :owner_id, 'SOURCE_COLLECTION', 'RUNNING', 1, 0, 'analysis:v1'"
                ")"
            ),
            {"id": job_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO job_checkpoints ("
                "id, job_id, owner_user_id, checkpoint_revision, checkpoint_schema_version, "
                "analysis_input_version, execution_fence, owner_deletion_epoch, resume_stage, "
                "policy_revision, state_ref, resume_payload, resumable"
                ") VALUES ("
                ":id, :job_id, :owner_id, 1, 'w2.collection.v1', 'analysis:v1', 1, 0, "
                ":resume_stage, :policy_revision, 'checkpoint://phase4', '{}'::jsonb, true"
                ")"
            ),
            {
                "id": uuid4(),
                "job_id": job_id,
                "owner_id": owner_id,
                "resume_stage": resume_stage,
                "policy_revision": policy_revision,
            },
        )


def test_migration_and_model_expose_the_effective_policy_revision(
    migrated_engine: Engine,
) -> None:
    inspector = inspect(migrated_engine)
    columns = {column["name"]: column for column in inspector.get_columns("job_checkpoints")}
    checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("job_checkpoints")
    }

    assert columns["policy_revision"]["nullable"] is True
    assert JobCheckpoint.policy_revision.property.columns[0].nullable is True
    assert any("policy_revision_positive" in name for name in checks)
    assert any("w2_non_policy_revision_required" in name for name in checks)


@pytest.mark.parametrize("invalid_revision", [None, 0, -1])
def test_non_policy_w2_checkpoint_requires_a_positive_effective_revision(
    migrated_engine: Engine,
    invalid_revision: int | None,
) -> None:
    with pytest.raises(IntegrityError):
        _insert_checkpoint(
            migrated_engine,
            resume_stage="fetch",
            policy_revision=invalid_revision,
        )


def test_policy_checkpoint_may_precede_the_effective_revision(
    migrated_engine: Engine,
) -> None:
    _insert_checkpoint(migrated_engine, resume_stage="policy", policy_revision=None)
    _insert_checkpoint(migrated_engine, resume_stage="fetch", policy_revision=7)
