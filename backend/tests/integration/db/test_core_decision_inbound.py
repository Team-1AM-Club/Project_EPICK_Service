from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from time import sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.application_workspace import Company
from app.models.identity import User
from app.models.jobs import (
    InboxReceipt,
    Job,
    JobCommand,
    JobCoreDecisionBinding,
    JobRequiredAction,
    OutboxMessage,
)
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source
from app.repo.core_decisions import CoreDecisionRepository
from app.repo.jobs import JobRepository
from app.runtime.core_decision_binding import CoreDecisionReceiptOutcome
from app.services.core_decision_inbound import CoreDecisionInboundService
from app.services.deletion import DeletionOrchestrationService
from app.services.jobs import JobActionStaleError, JobService


@pytest.fixture(autouse=True)
def clean_core_decision_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE inbox_receipts"))
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))


def _seed_waiting_job(session: Session) -> tuple[User, Company, Source, Job, JobRequiredAction]:
    owner = User(display_name="W3 inbound owner", locale="ko-KR", timezone="Asia/Seoul")
    company = Company(legal_name="Inbound Company", display_name="Inbound Company")
    session.add_all([owner, company])
    session.flush()
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url="https://example.test/core-inbound",
        canonical_url_hash=f"core-inbound-{uuid4()}",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    job = Job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        status="WAITING_USER",
        dispatch_status="BLOCKED",
        owner_deletion_epoch=0,
        analysis_input_version="knowledge-input:alpha",
    )
    session.add_all([source, job])
    session.flush()
    session.add(
        JobSourceLink(
            job_id=job.id,
            owner_user_id=owner.id,
            source_id=source.id,
            source_version_id=None,
            command_id=None,
            purpose_ref="SOURCE_COLLECTION",
            analysis_input_version=job.analysis_input_version,
        )
    )
    action = JobRequiredAction(
        job_id=job.id,
        owner_user_id=owner.id,
        action_code="CORE_DECISION_REQUIRED",
        action_status="OPEN",
        context_code="CORE_DECISION_BINDING_MISMATCH",
        expected_input_version=job.analysis_input_version,
    )
    session.add(action)
    session.flush()
    return owner, company, source, job, action


def _core_event(*, company: Company, source: Source, job: Job) -> dict[str, object]:
    return {
        "schema_version": "w3.private.core-decision/0.1-candidate",
        "message_type": "w3.private.w1.core-decision",
        "message_id": str(uuid4()),
        "occurred_at": "2026-09-18T00:00:00Z",
        "visibility_scope": "PRIVATE",
        "producer": "w3",
        "job_id": str(job.id),
        "company_id": str(company.id),
        "source_id": str(source.id),
        "analysis_input_version": job.analysis_input_version,
        "decision_scope": "COMPANY_KNOWLEDGE",
        "decision_owner": "W3",
        "question_version_id": None,
        "decision_version": 1,
        "is_core": True,
        "decision_code": "CORE_REQUIRED",
        "reason_code": "REQUIRED_COMPANY_EVIDENCE",
    }


@pytest.mark.postgres
def test_valid_core_event_is_atomic_and_does_not_auto_dispatch(db_session: Session) -> None:
    owner, company, source, job, missing_action = _seed_waiting_job(db_session)
    event = _core_event(company=company, source=source, job=job)

    receipt = CoreDecisionInboundService(db_session).apply(
        body=event,
        authenticated_principal="w3",
    )
    db_session.flush()

    assert receipt.outcome is CoreDecisionReceiptOutcome.APPLIED
    assert receipt.decision_id is not None
    assert db_session.scalar(select(func.count()).select_from(InboxReceipt)) == 1
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCommand)) == 0
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage)) == 0

    db_session.refresh(job)
    db_session.refresh(missing_action)
    assert job.status == "WAITING_USER"
    assert job.owner_user_id == owner.id
    assert missing_action.action_status == "RESOLVED"
    retry = db_session.scalar(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job.id,
            JobRequiredAction.action_code == "RETRY",
            JobRequiredAction.action_status == "OPEN",
        )
    )
    assert retry is not None
    assert retry.expected_input_version == job.analysis_input_version

    binding = db_session.scalar(select(JobCoreDecisionBinding))
    assert binding is not None
    assert binding.owner_user_id == owner.id
    assert binding.source_id == source.id
    assert binding.analysis_source_decision_id == UUID(str(receipt.decision_id))
    assert binding.origin_message_id == UUID(str(event["message_id"]))


@pytest.mark.postgres
def test_exact_duplicate_and_same_id_changed_body_do_not_mutate_domain_rows(
    db_session: Session,
) -> None:
    _, company, source, job, _ = _seed_waiting_job(db_session)
    event = _core_event(company=company, source=source, job=job)
    service = CoreDecisionInboundService(db_session)
    applied = service.apply(body=event, authenticated_principal="w3")
    baseline = {
        model: db_session.scalar(select(func.count()).select_from(model))
        for model in (
            AnalysisSourceDecision,
            JobCoreDecisionBinding,
            JobRequiredAction,
            JobCommand,
            OutboxMessage,
        )
    }

    duplicate = service.apply(body=event, authenticated_principal="w3")
    changed = dict(event)
    changed["reason_code"] = "ALTERED_SAME_MESSAGE"
    conflict = service.apply(body=changed, authenticated_principal="w3")

    assert applied.outcome is CoreDecisionReceiptOutcome.APPLIED
    assert duplicate.outcome is CoreDecisionReceiptOutcome.DUPLICATE
    assert duplicate.decision_id == applied.decision_id
    assert conflict.outcome is CoreDecisionReceiptOutcome.REJECTED_CONFLICT
    for model, count in baseline.items():
        assert db_session.scalar(select(func.count()).select_from(model)) == count


def _reopen_missing_decision_action(session: Session, *, job: Job) -> None:
    for action in session.scalars(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job.id,
            JobRequiredAction.action_status == "OPEN",
        )
    ):
        action.action_status = "DISMISSED"
        action.resolved_at = datetime.now(UTC)
    session.add(
        JobRequiredAction(
            job_id=job.id,
            owner_user_id=job.owner_user_id,
            action_code="CORE_DECISION_REQUIRED",
            action_status="OPEN",
            context_code="CORE_DECISION_BINDING_MISMATCH",
            expected_input_version=job.analysis_input_version,
        )
    )
    session.flush()


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("incoming_revision", "expected_outcome", "expected_decisions"),
    [
        (1, CoreDecisionReceiptOutcome.STALE_DISCARDED, 1),
        (2, CoreDecisionReceiptOutcome.REJECTED_CONFLICT, 1),
        (5, CoreDecisionReceiptOutcome.APPLIED, 2),
    ],
)
def test_revision_currentness_allows_gaps_but_rejects_lower_and_same_revision(
    db_session: Session,
    incoming_revision: int,
    expected_outcome: CoreDecisionReceiptOutcome,
    expected_decisions: int,
) -> None:
    _, company, source, job, _ = _seed_waiting_job(db_session)
    first = _core_event(company=company, source=source, job=job)
    first["decision_version"] = 2
    service = CoreDecisionInboundService(db_session)
    service.apply(body=first, authenticated_principal="w3")
    _reopen_missing_decision_action(db_session, job=job)
    incoming = dict(first)
    incoming["message_id"] = str(uuid4())
    incoming["decision_version"] = incoming_revision

    result = service.apply(body=incoming, authenticated_principal="w3")

    assert result.outcome is expected_outcome
    assert db_session.scalar(
        select(func.count()).select_from(AnalysisSourceDecision)
    ) == expected_decisions
    assert db_session.scalar(
        select(func.count()).select_from(JobCoreDecisionBinding)
    ) == expected_decisions
    assert db_session.scalar(select(func.count()).select_from(JobCommand)) == 0
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage)) == 0


@pytest.mark.postgres
@pytest.mark.parametrize("mismatch", ["company", "source", "input", "principal"])
def test_wrong_authenticated_or_domain_binding_is_terminal_without_domain_write(
    db_session: Session,
    mismatch: str,
) -> None:
    _, company, source, job, _ = _seed_waiting_job(db_session)
    event = _core_event(company=company, source=source, job=job)
    principal = "w3"
    if mismatch == "company":
        event["company_id"] = str(uuid4())
    elif mismatch == "source":
        event["source_id"] = str(uuid4())
    elif mismatch == "input":
        event["analysis_input_version"] = "knowledge-input:stale"
    else:
        principal = "w4"

    result = CoreDecisionInboundService(db_session).apply(
        body=event,
        authenticated_principal=principal,
    )

    expected = (
        CoreDecisionReceiptOutcome.REJECTED_PRINCIPAL
        if mismatch == "principal"
        else CoreDecisionReceiptOutcome.REJECTED_BINDING
    )
    assert result.outcome is expected
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 0
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 0
    assert db_session.scalar(select(func.count()).select_from(JobCommand)) == 0
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage)) == 0


@pytest.mark.postgres
def test_non_core_decision_stays_waiting_and_exposes_only_stop(db_session: Session) -> None:
    _, company, source, job, missing_action = _seed_waiting_job(db_session)
    event = _core_event(company=company, source=source, job=job)
    event.update(
        {
            "is_core": False,
            "decision_code": "NON_CORE_OPTIONAL",
            "reason_code": "OPTIONAL_COMPANY_EVIDENCE",
        }
    )

    receipt = CoreDecisionInboundService(db_session).apply(
        body=event,
        authenticated_principal="w3",
    )
    db_session.flush()

    assert receipt.outcome is CoreDecisionReceiptOutcome.APPLIED
    db_session.refresh(job)
    db_session.refresh(missing_action)
    assert job.status == "WAITING_USER"
    assert missing_action.action_status == "RESOLVED"
    actions = list(
        db_session.scalars(
            select(JobRequiredAction).where(
                JobRequiredAction.job_id == job.id,
                JobRequiredAction.action_status == "OPEN",
            )
        )
    )
    assert [action.action_code for action in actions] == ["STOP"]
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCommand)) == 0
    assert db_session.scalar(select(func.count()).select_from(OutboxMessage)) == 0


@pytest.mark.postgres
@pytest.mark.parametrize("winner", ["cancel", "decision"])
def test_cancellation_commit_order_never_reactivates_a_job(
    db_session: Session,
    winner: str,
) -> None:
    owner, company, source, job, _ = _seed_waiting_job(db_session)
    event = _core_event(company=company, source=source, job=job)
    db_session.commit()

    if winner == "cancel":
        JobService(db_session).request_cancellation(owner_user_id=owner.id, job_id=job.id)
        db_session.commit()
        first = CoreDecisionInboundService(db_session).apply(
            body=event,
            authenticated_principal="w3",
        )
        assert first.outcome is CoreDecisionReceiptOutcome.REJECTED_BINDING
    else:
        first = CoreDecisionInboundService(db_session).apply(
            body=event,
            authenticated_principal="w3",
        )
        assert first.outcome is CoreDecisionReceiptOutcome.APPLIED
        db_session.commit()
        JobService(db_session).request_cancellation(owner_user_id=owner.id, job_id=job.id)
        db_session.commit()

    replay = CoreDecisionInboundService(db_session).apply(
        body=event,
        authenticated_principal="w3",
    )
    db_session.commit()
    db_session.expire_all()

    stored_job = db_session.get(Job, job.id)
    assert replay.outcome is CoreDecisionReceiptOutcome.DUPLICATE
    assert stored_job is not None and stored_job.status == "CANCELLED"
    assert list(
        db_session.scalars(
            select(JobRequiredAction).where(
                JobRequiredAction.job_id == job.id,
                JobRequiredAction.action_status == "OPEN",
            )
        )
    ) == []
    assert db_session.scalar(select(func.count()).select_from(JobCommand)) == 0


@pytest.mark.postgres
def test_cancelled_job_rejects_previously_open_core_retry_even_with_binding(
    db_session: Session,
) -> None:
    owner, company, source, job, _ = _seed_waiting_job(db_session)
    event = _core_event(company=company, source=source, job=job)
    CoreDecisionInboundService(db_session).apply(body=event, authenticated_principal="w3")
    retry = db_session.scalar(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job.id,
            JobRequiredAction.action_code == "RETRY",
        )
    )
    assert retry is not None
    expected_input_version = retry.expected_input_version
    expected_result_version = retry.expected_result_version
    JobService(db_session).request_cancellation(owner_user_id=owner.id, job_id=job.id)

    with pytest.raises(JobActionStaleError):
        JobService(db_session).apply_required_action(
            owner_user_id=owner.id,
            job_id=job.id,
            required_action_id=retry.id,
            action_code="RETRY",
            expected_input_version=expected_input_version,
            expected_result_version=expected_result_version,
            acknowledge_rate_limit=False,
        )

    db_session.flush()
    assert job.status == "CANCELLED"
    assert retry.action_status == "DISMISSED"
    assert db_session.scalar(
        select(func.count()).select_from(JobCoreDecisionBinding)
    ) == 1
    assert db_session.scalar(select(func.count()).select_from(JobCommand)) == 0


@pytest.mark.postgres
@pytest.mark.parametrize("first_locker", ["decision", "cancel"])
def test_user_job_locks_serialize_live_decision_cancellation_race(
    migrated_engine: Engine,
    db_session: Session,
    first_locker: str,
) -> None:
    owner, company, source, job, _ = _seed_waiting_job(db_session)
    event = _core_event(company=company, source=source, job=job)
    owner_id = owner.id
    job_id = job.id
    db_session.commit()
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)

    def apply_in_thread() -> CoreDecisionReceiptOutcome:
        with factory.begin() as session:
            return CoreDecisionInboundService(session).apply(
                body=event,
                authenticated_principal="w3",
            ).outcome

    def cancel_in_thread() -> str:
        with factory.begin() as session:
            return JobService(session).request_cancellation(
                owner_user_id=owner_id,
                job_id=job_id,
            ).status

    with factory() as first_session, ThreadPoolExecutor(max_workers=1) as executor:
        if first_locker == "decision":
            first = CoreDecisionInboundService(first_session).apply(
                body=event,
                authenticated_principal="w3",
            )
            assert first.outcome is CoreDecisionReceiptOutcome.APPLIED
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
            assert contender_result is CoreDecisionReceiptOutcome.REJECTED_BINDING
            assert verification.scalar(
                select(func.count()).select_from(JobCoreDecisionBinding)
            ) == 0


@pytest.mark.postgres
@pytest.mark.parametrize("winner", ["deletion", "decision"])
def test_deletion_commit_order_removes_private_binding_and_preserves_shared_source(
    db_session: Session,
    winner: str,
) -> None:
    owner, company, source, job, _ = _seed_waiting_job(db_session)
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
    event = _core_event(company=company, source=source, job=job)
    db_session.commit()

    if winner == "decision":
        applied = CoreDecisionInboundService(db_session).apply(
            body=event,
            authenticated_principal="w3",
        )
        assert applied.outcome is CoreDecisionReceiptOutcome.APPLIED
        db_session.commit()

    deletion = DeletionOrchestrationService(db_session)
    preview = deletion.create_account_deletion_preview(
        owner_user_id=owner.id,
        preview_token=f"delete-{winner}",
    )
    deletion.confirm_and_start_account_deletion(
        owner_user_id=owner.id,
        deletion_request_id=preview.request.id,
        preview_token=preview.preview_token,
    )
    db_session.commit()

    result = CoreDecisionInboundService(db_session).apply(
        body=event,
        authenticated_principal="w3",
    )
    db_session.commit()
    db_session.expire_all()

    assert result.outcome in {
        CoreDecisionReceiptOutcome.REJECTED_BINDING,
        CoreDecisionReceiptOutcome.DUPLICATE,
    }
    assert db_session.scalar(
        select(func.count()).select_from(JobCoreDecisionBinding).where(
            JobCoreDecisionBinding.owner_user_id == owner.id
        )
    ) == 0
    assert db_session.get(Source, source.id) is not None
    assert db_session.get(JobSourceLink, other_link.id) is not None
    assert db_session.get(Job, other_job.id) is not None


@pytest.mark.postgres
@pytest.mark.parametrize("boundary", ["receipt", "decision", "binding", "action"])
def test_failure_after_each_write_boundary_rolls_back_every_inbound_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    _, company, source, job, _ = _seed_waiting_job(db_session)
    event = _core_event(company=company, source=source, job=job)
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

        def fail_after_action(
            repository: JobRepository, action: JobRequiredAction
        ) -> None:
            original_action(repository, action)
            repository.session.flush()
            raise RuntimeError("injected after action")

        monkeypatch.setattr(JobRepository, "add_required_action", fail_after_action)

    with pytest.raises(RuntimeError, match="injected"):
        CoreDecisionInboundService(db_session).apply(
            body=event,
            authenticated_principal="w3",
        )
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(InboxReceipt)) == 0
    assert db_session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) == 0
    assert db_session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) == 0
    actions = list(
        db_session.scalars(select(JobRequiredAction).where(JobRequiredAction.job_id == job.id))
    )
    assert len(actions) == 1
    assert actions[0].action_code == "CORE_DECISION_REQUIRED"
    assert actions[0].action_status == "OPEN"
