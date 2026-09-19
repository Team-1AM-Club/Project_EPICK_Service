from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.application_workspace import ApplicationProject, ProjectQuestion, QuestionVersion
from app.models.jobs import (
    InboxReceipt,
    JobCommand,
    JobCoreDecisionBinding,
    JobRequiredAction,
    OutboxMessage,
)
from app.models.sources import AnalysisSourceDecision
from app.runtime.w4_question_core_decision import W4QuestionCoreReceiptOutcome
from app.services.w4_question_core_inbound import (
    W4QuestionCoreInboundError,
    W4QuestionCoreInboundService,
)
from tests.integration.db.w4_question_core_support import (
    question_core_event,
    reopen_question_core_action,
    seed_question_job,
)


@pytest.fixture(autouse=True)
def clean_w4_question_core_tables(db_session: Session) -> None:
    db_session.execute(text("TRUNCATE inbox_receipts"))
    db_session.execute(text("TRUNCATE users CASCADE"))
    db_session.execute(text("TRUNCATE companies CASCADE"))


@pytest.mark.postgres
@pytest.mark.parametrize("is_core", (True, False))
def test_w4_valid_core_and_non_core_are_atomic_and_never_auto_dispatch(
    db_session: Session, is_core: bool
) -> None:
    owner, _, source, job, question_version, action = seed_question_job(db_session)
    receipt = W4QuestionCoreInboundService(db_session).apply(
        body=question_core_event(
            job=job, source=source, question_version=question_version, is_core=is_core
        ),
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    db_session.flush()

    assert receipt.outcome is W4QuestionCoreReceiptOutcome.APPLIED
    assert receipt.decision_id is not None
    assert db_session.scalar(select(func.count()).select_from(InboxReceipt)) == 1
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCommand)) == 0
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage)) == 0

    binding = db_session.scalar(select(JobCoreDecisionBinding))
    assert binding is not None
    assert binding.origin_producer == "w4"
    assert binding.decision_scope == "QUESTION_MATCHING"
    assert binding.question_version_id == question_version.id
    assert binding.origin_decision_id is not None
    db_session.refresh(action)
    assert action.action_status == "RESOLVED"
    next_action = db_session.scalar(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job.id,
            JobRequiredAction.action_status == "OPEN",
        )
    )
    assert next_action is not None
    assert next_action.action_code == ("RETRY" if is_core else "STOP")
    assert job.owner_user_id == owner.id


@pytest.mark.postgres
@pytest.mark.parametrize(
    "mutation",
    ("unknown_job", "wrong_project", "wrong_question", "wrong_source", "wrong_input"),
)
def test_w4_never_infers_or_accepts_a_stale_relation(
    db_session: Session, mutation: str
) -> None:
    owner, _, source, job, question_version, _ = seed_question_job(db_session)
    event = question_core_event(job=job, source=source, question_version=question_version)

    if mutation == "unknown_job":
        event["job_id"] = str(uuid4())
    elif mutation == "wrong_project":
        foreign_project = ApplicationProject(owner_user_id=owner.id)
        db_session.add(foreign_project)
        db_session.flush()
        job.project_id = foreign_project.id
    elif mutation == "wrong_question":
        question = db_session.scalar(
            select(ProjectQuestion).where(ProjectQuestion.id == question_version.question_id)
        )
        assert question is not None
        replacement = QuestionVersion(
            question_id=question.id,
            project_id=question.project_id,
            owner_user_id=owner.id,
            version_no=2,
            prompt="Replacement question",
            source="USER",
        )
        db_session.add(replacement)
        db_session.flush()
        question.current_version_id = replacement.id
    elif mutation == "wrong_source":
        event["source_id"] = str(uuid4())
    elif mutation == "wrong_input":
        event["analysis_input_version"] = "question-input:stale"

    service = W4QuestionCoreInboundService(db_session)
    if mutation == "unknown_job":
        with pytest.raises(W4QuestionCoreInboundError, match="W4_QUESTION_CORE_JOB_NOT_FOUND"):
            service.apply(
                body=event,
                authenticated_principal="w4-test-sender",
                expected_principal="w4-test-sender",
            )
        assert db_session.scalar(select(func.count()).select_from(InboxReceipt)) == 0
    else:
        receipt = service.apply(
            body=event,
            authenticated_principal="w4-test-sender",
            expected_principal="w4-test-sender",
        )
        assert receipt.outcome is W4QuestionCoreReceiptOutcome.REJECTED_BINDING
        assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 0
        assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 0


@pytest.mark.postgres
def test_w4_exact_delivery_replay_and_changed_body_reuse_the_first_receipt(
    db_session: Session,
) -> None:
    """An immutable delivery may be retried, but its first durable receipt wins."""

    _, _, source, job, question_version, _ = seed_question_job(db_session)
    event = question_core_event(job=job, source=source, question_version=question_version)
    service = W4QuestionCoreInboundService(db_session)

    applied = service.apply(
        body=event,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    duplicate = service.apply(
        body=event,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    changed = dict(event)
    changed["reason_code"] = "ALTERED_SAME_MESSAGE"
    conflict = service.apply(
        body=changed,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )

    stored = db_session.scalar(select(InboxReceipt))
    assert applied.outcome is W4QuestionCoreReceiptOutcome.APPLIED
    assert duplicate.outcome is W4QuestionCoreReceiptOutcome.DUPLICATE
    assert duplicate.decision_id == applied.decision_id
    assert conflict.outcome is W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT
    assert stored is not None and stored.outcome_code == W4QuestionCoreReceiptOutcome.APPLIED.value
    assert db_session.scalar(select(func.count()).select_from(InboxReceipt)) == 1
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 1


@pytest.mark.postgres
def test_w4_terminal_binding_replay_keeps_the_first_terminal_receipt(
    db_session: Session,
) -> None:
    _, _, source, job, question_version, _ = seed_question_job(db_session)
    event = question_core_event(job=job, source=source, question_version=question_version)
    event["analysis_input_version"] = "question-input:stale"
    service = W4QuestionCoreInboundService(db_session)

    rejected = service.apply(
        body=event,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    duplicate = service.apply(
        body=event,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    changed = dict(event)
    changed["reason_code"] = "ALTERED_TERMINAL_MESSAGE"
    conflict = service.apply(
        body=changed,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )

    stored = db_session.scalar(select(InboxReceipt))
    assert rejected.outcome is W4QuestionCoreReceiptOutcome.REJECTED_BINDING
    assert duplicate.outcome is W4QuestionCoreReceiptOutcome.DUPLICATE
    assert conflict.outcome is W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT
    assert stored is not None and stored.outcome_code == "REJECTED_BINDING"
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 0
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 0


@pytest.mark.postgres
def test_w4_same_origin_decision_with_a_new_delivery_is_a_durable_duplicate(
    db_session: Session,
) -> None:
    """W4 may resend its immutable decision after an ACK loss under a new message ID."""

    _, _, source, job, question_version, _ = seed_question_job(db_session)
    first = question_core_event(job=job, source=source, question_version=question_version)
    replay = dict(first)
    replay["message_id"] = str(uuid4())
    service = W4QuestionCoreInboundService(db_session)

    applied = service.apply(
        body=first,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    duplicate = service.apply(
        body=replay,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )

    receipts = list(db_session.scalars(select(InboxReceipt)))
    assert applied.outcome is W4QuestionCoreReceiptOutcome.APPLIED
    assert duplicate.outcome is W4QuestionCoreReceiptOutcome.DUPLICATE
    assert duplicate.decision_id == applied.decision_id
    assert sorted(receipt.outcome_code for receipt in receipts) == ["APPLIED", "DUPLICATE"]
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobRequiredAction)) == 2


@pytest.mark.postgres
def test_w4_same_origin_decision_with_changed_semantics_is_a_conflict(
    db_session: Session,
) -> None:
    _, _, source, job, question_version, _ = seed_question_job(db_session)
    first = question_core_event(job=job, source=source, question_version=question_version)
    changed = dict(first)
    changed["message_id"] = str(uuid4())
    changed["reason_code"] = "ALTERED_SAME_DECISION"
    service = W4QuestionCoreInboundService(db_session)

    service.apply(
        body=first,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    conflict = service.apply(
        body=changed,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )

    assert conflict.outcome is W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT
    assert conflict.error_code == "W4_QUESTION_CORE_DECISION_ID_CONFLICT"
    assert db_session.scalar(select(func.count()).select_from(InboxReceipt)) == 2
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 1


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("incoming_revision", "expected_outcome"),
    (
        (1, W4QuestionCoreReceiptOutcome.STALE_DISCARDED),
        (2, W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT),
    ),
)
def test_w4_lower_and_same_revisions_never_replace_the_current_binding(
    db_session: Session,
    incoming_revision: int,
    expected_outcome: W4QuestionCoreReceiptOutcome,
) -> None:
    _, _, source, job, question_version, _ = seed_question_job(db_session)
    first = question_core_event(job=job, source=source, question_version=question_version)
    first["decision_version"] = 2
    service = W4QuestionCoreInboundService(db_session)
    service.apply(
        body=first,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    reopen_question_core_action(db_session, job=job)
    incoming = dict(first)
    incoming["message_id"] = str(uuid4())
    incoming["decision_id"] = str(uuid4())
    incoming["decision_version"] = incoming_revision

    result = service.apply(
        body=incoming,
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )

    assert result.outcome is expected_outcome
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 1
