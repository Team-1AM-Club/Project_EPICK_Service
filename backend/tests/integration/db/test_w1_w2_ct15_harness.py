from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.identity import User
from app.models.jobs import Job, JobCommand
from app.models.sources import Source
from app.models.w2_commit_operations import W2CommitOperation
from app.runtime.w1_w2_ct15_harness import (
    Ct15HarnessError,
    cancel_primary,
    delete_primary,
    redrive_primary_gate_operation,
    seed_fixture,
    validate_run_id,
)
from app.services.w2_commit_gate import W2CommitGateService

pytestmark = pytest.mark.postgres

RUN_ID = "ct15-harness-01"


@pytest.fixture(autouse=True)
def _clean_ct15_harness_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))


def _factory(migrated_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)


def test_seeded_fixture_is_two_owner_and_shared_source(migrated_engine: Engine) -> None:
    with _factory(migrated_engine).begin() as session:
        fixture = seed_fixture(session=session, run_id=RUN_ID)
        result = fixture.as_safe_dict()

        assert result["fixture"] == "two_owners_one_shared_source"
        assert fixture.primary.source_id == fixture.secondary.source_id
        assert fixture.primary.owner_id != fixture.secondary.owner_id
        assert fixture.primary.command_id != fixture.secondary.command_id
        assert result["primary"]["w2_collection_command"]["command_id"] == str(
            fixture.primary.command_id
        )
        assert session.get(Source, fixture.primary.source_id) is not None
        assert session.get(Job, fixture.primary.job_id).status == "RUNNING"
        assert session.get(JobCommand, fixture.primary.command_id).status == "ENQUEUED"


def test_cancel_fences_the_primary_without_touching_the_secondary(migrated_engine: Engine) -> None:
    factory = _factory(migrated_engine)
    with factory.begin() as session:
        fixture = seed_fixture(session=session, run_id=RUN_ID)
    with factory.begin() as session:
        result = cancel_primary(session=session, run_id=RUN_ID)

        assert result == {
            "status": "ok",
            "run_id": RUN_ID,
            "action": "cancel_primary",
            "job_status": "CANCEL_REQUESTED",
            "execution_fence": 2,
        }
        primary = session.get(Job, fixture.primary.job_id)
        secondary = session.get(Job, fixture.secondary.job_id)
        assert primary is not None and primary.status == "CANCEL_REQUESTED"
        assert secondary is not None and secondary.status == "RUNNING"


def test_delete_advances_only_primary_epoch_and_retains_shared_source(
    migrated_engine: Engine,
) -> None:
    factory = _factory(migrated_engine)
    with factory.begin() as session:
        fixture = seed_fixture(session=session, run_id=RUN_ID)
        W2CommitGateService(session).create_prepare_operation_with_outbox(
            owner_user_id=fixture.primary.owner_id,
            job_id=fixture.primary.job_id,
            command_id=fixture.primary.command_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            execution_lease_id=fixture.primary.lease_id,
            result_digest="sha256:" + "a" * 64,
        )
    with factory.begin() as session:
        result = delete_primary(session=session, run_id=RUN_ID)

        primary_owner = session.get(User, fixture.primary.owner_id)
        secondary_owner = session.get(User, fixture.secondary.owner_id)
        operation = session.scalar(
            select(W2CommitOperation).where(
                W2CommitOperation.command_id == fixture.primary.command_id
            )
        )
        assert result["deletion_epoch"] == 1
        assert result["shared_source_retained"] is True
        assert primary_owner is not None and primary_owner.deletion_epoch == 1
        assert secondary_owner is not None and secondary_owner.account_status == "ACTIVE"
        assert session.get(Source, fixture.primary.source_id) is not None
        assert operation is not None and operation.state == "ABORT_PENDING"


def test_recovery_requeues_the_same_primary_gate_message(migrated_engine: Engine) -> None:
    factory = _factory(migrated_engine)
    with factory.begin() as session:
        fixture = seed_fixture(session=session, run_id=RUN_ID)
        accepted = W2CommitGateService(session).create_prepare_operation_with_outbox(
            owner_user_id=fixture.primary.owner_id,
            job_id=fixture.primary.job_id,
            command_id=fixture.primary.command_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            execution_lease_id=fixture.primary.lease_id,
            result_digest="sha256:" + "b" * 64,
        )
        assert accepted.prepare_outbox is not None
        original_message_id = accepted.prepare_outbox.id
    with factory.begin() as session:
        result = redrive_primary_gate_operation(session=session, run_id=RUN_ID)

        assert result["requeued"] is True
        assert UUID(str(result["message_id"])) == original_message_id


@pytest.mark.parametrize("run_id", ("CT15-upper", "t059-not-ct15", "ct15_underscores"))
def test_harness_rejects_non_synthetic_run_ids(run_id: str) -> None:
    with pytest.raises(Ct15HarnessError, match="ct15-"):
        validate_run_id(run_id)
