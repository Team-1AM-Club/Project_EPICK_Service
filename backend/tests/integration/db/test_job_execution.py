from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.jobs import Job, JobRequiredAction, OwnerExecutionSlot
from app.services.application_workspace import ApplicationWorkspaceService
from app.services.idempotency import IdempotencyConflictError
from app.services.jobs import JobInputReference, JobService


@pytest.fixture(autouse=True)
def clean_job_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


def _create_owner(db_session: Session, display_name: str) -> User:
    user = User(display_name=display_name, locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(user)
    db_session.flush()
    return user


def _accept_and_enqueue(service: JobService, *, owner_user_id, number: int, input_refs=()):
    acceptance = service.accept_job(
        owner_user_id=owner_user_id,
        job_type="MATERIAL_SELECTION",
        idempotency_key=f"job-{number}",
        request_hash=f"{number:064x}",
        input_refs=input_refs,
    )
    service.mark_dispatch_enqueued(owner_user_id=owner_user_id, job_id=acceptance.job.id)
    return acceptance


def test_job_acceptance_is_atomic_replay_safe_and_separate_from_actual_dispatch(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Job owner")
    service = JobService(db_session)
    first = service.accept_job(
        owner_user_id=owner.id,
        job_type="MATERIAL_SELECTION",
        idempotency_key="acceptance-key",
        request_hash="a" * 64,
        input_refs=(JobInputReference(policy_name="selection-policy", policy_version="1"),),
    )
    db_session.commit()

    assert first.replayed is False
    assert first.job.status == "QUEUED"
    assert first.job.dispatch_status == "OUTBOX_PENDING"
    assert first.command is not None
    assert first.command.status == "PENDING"
    assert first.outbox_message is not None
    assert first.outbox_message.status == "PENDING"
    assert first.outbox_message.payload == {
        "command_id": str(first.command.id),
        "job_id": str(first.job.id),
        "execution_fence": 1,
        "owner_deletion_epoch": 0,
    }
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OwnerExecutionSlot)
            .where(OwnerExecutionSlot.owner_user_id == owner.id)
        )
        == 3
    )

    replay = service.accept_job(
        owner_user_id=owner.id,
        job_type="MATERIAL_SELECTION",
        idempotency_key="acceptance-key",
        request_hash="a" * 64,
    )
    assert replay.replayed is True
    assert replay.job.id == first.job.id
    assert replay.job.dispatch_status == "OUTBOX_PENDING"

    with pytest.raises(IdempotencyConflictError):
        service.accept_job(
            owner_user_id=owner.id,
            job_type="MATERIAL_SELECTION",
            idempotency_key="acceptance-key",
            request_hash="b" * 64,
        )


def test_fourth_execution_claim_waits_and_cancel_ack_releases_only_after_worker_ack(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Slot owner")
    service = JobService(db_session)
    accepted = [
        _accept_and_enqueue(service, owner_user_id=owner.id, number=number) for number in range(4)
    ]
    db_session.commit()

    leases = [
        service.claim_execution(
            owner_user_id=owner.id,
            job_id=acceptance.job.id,
            worker_ref="worker-a",
        )
        for acceptance in accepted
    ]
    db_session.commit()

    assert all(lease is not None for lease in leases[:3])
    assert leases[3] is None
    running_job = accepted[0].job
    active_lease = leases[0]
    assert active_lease is not None
    original_fence = active_lease.execution_fence

    service.request_cancellation(owner_user_id=owner.id, job_id=running_job.id)
    db_session.commit()
    db_session.refresh(running_job)
    assert running_job.status == "CANCEL_REQUESTED"
    assert running_job.execution_fence == original_fence + 1
    assert service.claim_execution(owner_user_id=owner.id, job_id=accepted[3].job.id) is None

    assert (
        service.commit_result(
            owner_user_id=owner.id,
            job_id=running_job.id,
            lease_id=active_lease.id,
            execution_fence=original_fence,
            owner_deletion_epoch=active_lease.owner_deletion_epoch,
        )
        is False
    )
    assert service.acknowledge_cancellation(owner_user_id=owner.id, job_id=running_job.id) is True
    db_session.commit()

    replacement_lease = service.claim_execution(
        owner_user_id=owner.id, job_id=accepted[3].job.id, worker_ref="worker-b"
    )
    assert replacement_lease is not None


def test_late_result_with_a_stale_owner_deletion_epoch_cannot_mutate_running_job(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Epoch owner")
    service = JobService(db_session)
    acceptance = _accept_and_enqueue(service, owner_user_id=owner.id, number=1)
    db_session.commit()
    lease = service.claim_execution(owner_user_id=owner.id, job_id=acceptance.job.id)
    db_session.commit()
    assert lease is not None

    owner.deletion_epoch = 1
    db_session.commit()
    assert (
        service.commit_result(
            owner_user_id=owner.id,
            job_id=acceptance.job.id,
            lease_id=lease.id,
            execution_fence=lease.execution_fence,
            owner_deletion_epoch=lease.owner_deletion_epoch,
        )
        is False
    )
    db_session.rollback()
    db_session.refresh(acceptance.job)
    assert acceptance.job.status == "RUNNING"
    assert acceptance.job.active_lease_id == lease.id


def test_explicit_retry_uses_a_new_fence_command_and_idempotency_key(db_session: Session) -> None:
    owner = _create_owner(db_session, "Retry owner")
    service = JobService(db_session)
    acceptance = _accept_and_enqueue(service, owner_user_id=owner.id, number=1)
    db_session.commit()
    lease = service.claim_execution(owner_user_id=owner.id, job_id=acceptance.job.id)
    db_session.commit()
    assert lease is not None

    assert (
        service.commit_result(
            owner_user_id=owner.id,
            job_id=acceptance.job.id,
            lease_id=lease.id,
            execution_fence=lease.execution_fence,
            owner_deletion_epoch=lease.owner_deletion_epoch,
            final_status="FAILED_RETRYABLE",
            completeness="partial",
        )
        is True
    )
    db_session.commit()
    db_session.refresh(acceptance.job)
    assert acceptance.job.status == "FAILED_RETRYABLE"
    assert acceptance.job.dispatch_status == "BLOCKED"
    assert acceptance.job.retryable is True

    retry = service.retry_job(
        owner_user_id=owner.id,
        job_id=acceptance.job.id,
        idempotency_key="retry-key",
        request_hash="d" * 64,
    )
    db_session.commit()
    assert retry.replayed is False
    assert retry.job.status == "QUEUED"
    assert retry.job.dispatch_status == "OUTBOX_PENDING"
    assert retry.job.execution_fence == lease.execution_fence + 1
    assert retry.command is not None
    assert retry.command.command_sequence == 2

    replay = service.retry_job(
        owner_user_id=owner.id,
        job_id=acceptance.job.id,
        idempotency_key="retry-key",
        request_hash="d" * 64,
    )
    assert replay.replayed is True
    assert replay.command is not None
    assert replay.command.id == retry.command.id
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(JobRequiredAction)
            .where(
                JobRequiredAction.job_id == acceptance.job.id,
                JobRequiredAction.action_code == "RETRY",
                JobRequiredAction.resolved_at.is_(None),
            )
        )
        == 0
    )


def test_paused_job_releases_its_slot_and_can_be_retried_after_user_action(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Paused slot owner")
    service = JobService(db_session)
    accepted = [
        _accept_and_enqueue(service, owner_user_id=owner.id, number=number) for number in range(4)
    ]
    db_session.commit()
    leases = [
        service.claim_execution(owner_user_id=owner.id, job_id=acceptance.job.id)
        for acceptance in accepted[:3]
    ]
    db_session.commit()
    paused_lease = leases[0]
    assert paused_lease is not None

    assert (
        service.pause_execution(
            owner_user_id=owner.id,
            job_id=accepted[0].job.id,
            lease_id=paused_lease.id,
            execution_fence=paused_lease.execution_fence,
            owner_deletion_epoch=paused_lease.owner_deletion_epoch,
            status="PAUSED_RATE_LIMIT",
            action_code="RETRY",
        )
        is True
    )
    db_session.commit()
    db_session.refresh(accepted[0].job)
    assert accepted[0].job.status == "PAUSED_RATE_LIMIT"
    assert accepted[0].job.active_lease_id is None
    assert service.claim_execution(owner_user_id=owner.id, job_id=accepted[3].job.id) is not None

    resumed = service.retry_job(
        owner_user_id=owner.id,
        job_id=accepted[0].job.id,
        idempotency_key="rate-limit-retry",
        request_hash="e" * 64,
    )
    assert resumed.job.status == "QUEUED"
    assert resumed.job.execution_fence == paused_lease.execution_fence + 1


def test_uncommitted_acceptance_leaves_no_job_and_inbox_receipt_deduplicates(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Atomic owner")
    db_session.commit()
    service = JobService(db_session)
    abandoned = service.accept_job(
        owner_user_id=owner.id,
        job_type="MATERIAL_SELECTION",
        idempotency_key="rollback-key",
        request_hash="f" * 64,
    )
    abandoned_job_id = abandoned.job.id
    db_session.rollback()

    assert db_session.get(Job, abandoned_job_id) is None
    accepted = service.accept_job(
        owner_user_id=owner.id,
        job_type="MATERIAL_SELECTION",
        idempotency_key="rollback-key",
        request_hash="f" * 64,
    )
    assert accepted.replayed is False
    event_id = uuid4()
    assert (
        service.record_inbox_receipt(
            consumer_name="w2-result-consumer", event_id=event_id, outcome_code="APPLIED"
        )
        is True
    )
    assert (
        service.record_inbox_receipt(
            consumer_name="w2-result-consumer", event_id=event_id, outcome_code="APPLIED"
        )
        is False
    )


def test_job_input_reference_cannot_cross_an_owner_boundary(db_session: Session) -> None:
    owner = _create_owner(db_session, "Input owner")
    other_owner = _create_owner(db_session, "Other input owner")
    service = JobService(db_session)
    workspace_service = ApplicationWorkspaceService(db_session)
    company = workspace_service.create_company(legal_name="EPICK", display_name="EPICK")
    other_project = workspace_service.create_project(
        owner_user_id=other_owner.id,
        company_id=company.id,
        title="Other owner's project",
        role_name="Backend engineer",
    )
    acceptance = _accept_and_enqueue(service, owner_user_id=owner.id, number=1)
    db_session.commit()

    # A real Version from the other owner cannot be used as this owner's typed Job input.
    with pytest.raises(IntegrityError):
        service.accept_job(
            owner_user_id=owner.id,
            job_type="MATERIAL_SELECTION",
            idempotency_key="cross-owner-input",
            request_hash="c" * 64,
            input_refs=(JobInputReference(project_version_id=other_project.current_version_id),),
        )
    db_session.rollback()

    assert db_session.get(Job, acceptance.job.id) is not None
    assert other_owner.id != owner.id
