from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from time import sleep

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.identity import User
from app.models.jobs import Job, JobCommand, JobCoreDecisionBinding
from app.models.sources import JobSourceLink, Source
from app.runtime.w4_question_core_decision import W4QuestionCoreReceiptOutcome
from app.services.deletion import DeletionOrchestrationService
from app.services.jobs import JobService
from app.services.w4_question_core_inbound import W4QuestionCoreInboundService
from tests.integration.db.w4_question_core_support import question_core_event, seed_question_job


@pytest.fixture(autouse=True)
def clean_w4_question_core_race_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE inbox_receipts"))
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))


@pytest.mark.postgres
@pytest.mark.parametrize("winner", ("cancel", "decision"))
def test_w4_cancellation_commit_order_never_restores_a_waiting_job(
    db_session: Session,
    winner: str,
) -> None:
    owner, _, source, job, question_version, _ = seed_question_job(db_session)
    event = question_core_event(job=job, source=source, question_version=question_version)
    db_session.commit()

    if winner == "cancel":
        JobService(db_session).request_cancellation(owner_user_id=owner.id, job_id=job.id)
        db_session.commit()
        first = W4QuestionCoreInboundService(db_session).apply(
            body=event,
            authenticated_principal="w4-test-sender",
            expected_principal="w4-test-sender",
        )
        assert first.outcome is W4QuestionCoreReceiptOutcome.REJECTED_BINDING
    else:
        first = W4QuestionCoreInboundService(db_session).apply(
            body=event,
            authenticated_principal="w4-test-sender",
            expected_principal="w4-test-sender",
        )
        assert first.outcome is W4QuestionCoreReceiptOutcome.APPLIED
        db_session.commit()
        JobService(db_session).request_cancellation(owner_user_id=owner.id, job_id=job.id)
        db_session.commit()

    replay = W4QuestionCoreInboundService(db_session).apply(
        body=event,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    db_session.commit()
    db_session.expire_all()

    stored_job = db_session.get(Job, job.id)
    assert replay.outcome is W4QuestionCoreReceiptOutcome.DUPLICATE
    assert stored_job is not None and stored_job.status == "CANCELLED"
    assert db_session.scalar(select(func.count()).select_from(JobCommand)) == 0


@pytest.mark.postgres
@pytest.mark.parametrize("first_locker", ("decision", "cancel"))
def test_w4_user_and_job_locks_serialize_live_cancellation_race(
    migrated_engine: Engine,
    db_session: Session,
    first_locker: str,
) -> None:
    owner, _, source, job, question_version, _ = seed_question_job(db_session)
    event = question_core_event(job=job, source=source, question_version=question_version)
    owner_id = owner.id
    job_id = job.id
    db_session.commit()
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)

    def apply_in_thread() -> W4QuestionCoreReceiptOutcome:
        with factory.begin() as session:
            return W4QuestionCoreInboundService(session).apply(
                body=event,
                authenticated_principal="w4-test-sender",
                expected_principal="w4-test-sender",
            ).outcome

    def cancel_in_thread() -> str:
        with factory.begin() as session:
            return JobService(session).request_cancellation(
                owner_user_id=owner_id,
                job_id=job_id,
            ).status

    with factory() as first_session, ThreadPoolExecutor(max_workers=1) as executor:
        if first_locker == "decision":
            first = W4QuestionCoreInboundService(first_session).apply(
                body=event,
                authenticated_principal="w4-test-sender",
                expected_principal="w4-test-sender",
            )
            assert first.outcome is W4QuestionCoreReceiptOutcome.APPLIED
            contender = executor.submit(cancel_in_thread)
        else:
            assert JobService(first_session).request_cancellation(
                owner_user_id=owner_id,
                job_id=job_id,
            ).status == "CANCELLED"
            contender = executor.submit(apply_in_thread)

        sleep(0.15)
        assert not contender.done()
        first_session.commit()
        contender_result = contender.result(timeout=5)

    with factory() as verification:
        stored_job = verification.get(Job, job_id)
        assert stored_job is not None and stored_job.status == "CANCELLED"
        assert verification.scalar(select(func.count()).select_from(JobCommand)) == 0
        if first_locker == "decision":
            assert contender_result == "CANCELLED"
            assert verification.scalar(
                select(func.count()).select_from(JobCoreDecisionBinding)
            ) == 1
        else:
            assert contender_result is W4QuestionCoreReceiptOutcome.REJECTED_BINDING
            assert verification.scalar(
                select(func.count()).select_from(JobCoreDecisionBinding)
            ) == 0


@pytest.mark.postgres
@pytest.mark.parametrize("winner", ("deletion", "decision"))
def test_w4_deletion_removes_only_private_owner_binding_and_preserves_shared_source(
    db_session: Session,
    winner: str,
) -> None:
    owner, _, source, job, question_version, _ = seed_question_job(db_session)
    other_owner = User(
        display_name="Shared source owner",
        locale="ko-KR",
        timezone="Asia/Seoul",
    )
    db_session.add(other_owner)
    db_session.flush()
    other_job = Job(
        owner_user_id=other_owner.id,
        job_type="SOURCE_COLLECTION",
        status="WAITING_USER",
        dispatch_status="BLOCKED",
        owner_deletion_epoch=0,
        analysis_input_version="shared-input:v1",
    )
    db_session.add(other_job)
    db_session.flush()
    other_link = JobSourceLink(
        job_id=other_job.id,
        owner_user_id=other_owner.id,
        source_id=source.id,
        source_version_id=None,
        command_id=None,
        purpose_ref="SOURCE_COLLECTION",
        analysis_input_version=other_job.analysis_input_version,
    )
    db_session.add(other_link)
    event = question_core_event(job=job, source=source, question_version=question_version)
    db_session.commit()

    if winner == "decision":
        applied = W4QuestionCoreInboundService(db_session).apply(
            body=event,
            authenticated_principal="w4-test-sender",
            expected_principal="w4-test-sender",
        )
        assert applied.outcome is W4QuestionCoreReceiptOutcome.APPLIED
        db_session.commit()

    deletion = DeletionOrchestrationService(db_session)
    preview = deletion.create_account_deletion_preview(
        owner_user_id=owner.id,
        preview_token=f"w4-delete-{winner}",
    )
    deletion.confirm_and_start_account_deletion(
        owner_user_id=owner.id,
        deletion_request_id=preview.request.id,
        preview_token=preview.preview_token,
    )
    db_session.commit()

    replay = W4QuestionCoreInboundService(db_session).apply(
        body=event,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    db_session.commit()
    db_session.expire_all()

    assert replay.outcome in {
        W4QuestionCoreReceiptOutcome.REJECTED_BINDING,
        W4QuestionCoreReceiptOutcome.DUPLICATE,
    }
    assert db_session.scalar(
        select(func.count())
        .select_from(JobCoreDecisionBinding)
        .where(JobCoreDecisionBinding.owner_user_id == owner.id)
    ) == 0
    assert db_session.get(Source, source.id) is not None
    assert db_session.get(JobSourceLink, other_link.id) is not None
    assert db_session.get(Job, other_job.id) is not None
    # A deletion can remove the deleted owner's personal delivery history.
    # The safety boundary is that it must not remove the shared Source or the
    # other owner's relationship to it, both asserted above.
