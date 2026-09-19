from __future__ import annotations

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.jobs import InboxReceipt, JobCoreDecisionBinding, JobRequiredAction
from app.models.sources import AnalysisSourceDecision
from app.repo.core_decisions import CoreDecisionRepository
from app.repo.jobs import JobRepository
from app.services.w4_question_core_inbound import W4QuestionCoreInboundService
from tests.integration.db.w4_question_core_support import question_core_event, seed_question_job


@pytest.fixture(autouse=True)
def clean_w4_question_core_atomicity_tables(db_session: Session) -> None:
    db_session.execute(text("TRUNCATE inbox_receipts"))
    db_session.execute(text("TRUNCATE users CASCADE"))
    db_session.execute(text("TRUNCATE companies CASCADE"))


@pytest.mark.postgres
@pytest.mark.parametrize("boundary", ("receipt", "decision", "binding", "action"))
def test_w4_failure_after_each_write_boundary_rolls_back_every_inbound_effect(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    """A delivery is retryable unless its whole receipt effect commits together."""

    _, _, source, job, question_version, required_action = seed_question_job(db_session)
    event = question_core_event(job=job, source=source, question_version=question_version)
    db_session.commit()

    if boundary == "receipt":
        original = JobRepository.reserve_inbox_receipt

        def fail_after_receipt(repository: JobRepository, **kwargs: object) -> object:
            original(repository, **kwargs)
            repository.session.flush()
            raise RuntimeError("injected after receipt")

        monkeypatch.setattr(JobRepository, "reserve_inbox_receipt", fail_after_receipt)
    elif boundary == "decision":
        original_decision = CoreDecisionRepository.add_decision

        def fail_after_decision(
            repository: CoreDecisionRepository, decision: AnalysisSourceDecision
        ) -> None:
            original_decision(repository, decision)
            repository.session.flush()
            raise RuntimeError("injected after decision")

        monkeypatch.setattr(CoreDecisionRepository, "add_decision", fail_after_decision)
    elif boundary == "binding":
        original_binding = CoreDecisionRepository.add_binding

        def fail_after_binding(
            repository: CoreDecisionRepository, binding: JobCoreDecisionBinding
        ) -> None:
            original_binding(repository, binding)
            repository.session.flush()
            raise RuntimeError("injected after binding")

        monkeypatch.setattr(CoreDecisionRepository, "add_binding", fail_after_binding)
    else:
        original_action = JobRepository.add_required_action

        def fail_after_action(repository: JobRepository, action: JobRequiredAction) -> None:
            original_action(repository, action)
            repository.session.flush()
            raise RuntimeError("injected after action")

        monkeypatch.setattr(JobRepository, "add_required_action", fail_after_action)

    with pytest.raises(RuntimeError, match="injected"):
        W4QuestionCoreInboundService(db_session).apply(
            body=event,
            authenticated_principal="w4-test-sender",
            expected_principal="w4-test-sender",
        )
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(InboxReceipt)) == 0
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 0
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 0
    actions = list(
        db_session.scalars(
            select(JobRequiredAction).where(JobRequiredAction.job_id == job.id)
        )
    )
    assert [(action.id, action.action_status) for action in actions] == [
        (required_action.id, "OPEN")
    ]
