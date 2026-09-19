from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    Company,
    ProjectQuestion,
    QuestionVersion,
)
from app.models.identity import User
from app.models.jobs import (
    Job,
    JobCommand,
    JobCoreDecisionBinding,
    JobRequiredAction,
    OwnerExecutionSlot,
)
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source
from app.runtime.core_decision_binding import project_core_decision_pin
from app.runtime.lookup_adapter import LookupRequest, _lookup_command
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.runtime.question_core_binding import (
    QuestionCoreBindingError,
    resolve_question_collection_company,
)
from app.runtime.sqs import InMemorySqsPort
from app.runtime.workers import JobWorker
from app.services.jobs import JobService
from app.services.w4_question_core_inbound import W4QuestionCoreInboundService

EXECUTION_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w1-question-core-execution"
W2_COMMAND_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w1-question-core-w2"
BACKEND_ROOT = Path(__file__).parents[3]
RUNTIME_ROLE_TEMPLATE_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_roles.sql"
RUNTIME_PRIVILEGES_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_privileges.sql"


@pytest.fixture(autouse=True)
def clean_question_core_binding_tables(db_session: Session) -> None:
    db_session.execute(text("TRUNCATE inbox_receipts"))
    db_session.execute(text("TRUNCATE users CASCADE"))
    db_session.execute(text("TRUNCATE companies CASCADE"))


def _seed_accepted_question_core(
    session: Session,
) -> tuple[
    User,
    Company,
    ApplicationProject,
    ProjectQuestion,
    Source,
    Job,
    QuestionVersion,
    JobSourceLink,
    AnalysisSourceDecision,
    JobCoreDecisionBinding,
]:
    owner = User(display_name="Question binding owner", locale="ko-KR", timezone="Asia/Seoul")
    company = Company(legal_name="Question Binding Co", display_name="Question Binding Co")
    session.add_all((owner, company))
    session.flush()
    session.add_all(
        OwnerExecutionSlot(owner_user_id=owner.id, slot_no=slot_no)
        for slot_no in (1, 2, 3)
    )
    project = ApplicationProject(owner_user_id=owner.id)
    session.add(project)
    session.flush()
    project_version = ApplicationProjectVersion(
        project_id=project.id,
        owner_user_id=owner.id,
        version_no=1,
        company_id=company.id,
        title="Question binding project",
        role_name="Engineer",
    )
    session.add(project_version)
    session.flush()
    project.current_version_id = project_version.id
    question = ProjectQuestion(owner_user_id=owner.id, project_id=project.id, display_order=0)
    session.add(question)
    session.flush()
    question_version = QuestionVersion(
        question_id=question.id,
        project_id=project.id,
        owner_user_id=owner.id,
        version_no=1,
        prompt="Explain the relevant project.",
        source="USER",
    )
    session.add(question_version)
    session.flush()
    question.current_version_id = question_version.id
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url="https://example.test/question-core-binding",
        canonical_url_hash=f"question-core-binding-{uuid4()}",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    job = Job(
        owner_user_id=owner.id,
        project_id=project.id,
        job_type="SOURCE_COLLECTION",
        status="WAITING_USER",
        dispatch_status="BLOCKED",
        owner_deletion_epoch=0,
        analysis_input_version="question-input:alpha",
    )
    session.add_all((source, job))
    session.flush()
    source_link = JobSourceLink(
        job_id=job.id,
        owner_user_id=owner.id,
        source_id=source.id,
        source_version_id=None,
        command_id=None,
        purpose_ref="SOURCE_COLLECTION",
        analysis_input_version=job.analysis_input_version,
    )
    session.add_all(
        (
            source_link,
            JobRequiredAction(
                job_id=job.id,
                owner_user_id=owner.id,
                action_code="CORE_DECISION_REQUIRED",
                action_status="OPEN",
                context_code="CORE_DECISION_BINDING_MISMATCH",
                expected_input_version=job.analysis_input_version,
            ),
        )
    )
    session.flush()
    receipt = W4QuestionCoreInboundService(session).apply(
        body={
            "schema_version": "w4.private.question-core-decision/0.1-candidate",
            "message_type": "w4.private.w1.question-core-decision",
            "message_id": str(uuid4()),
            "decision_id": str(uuid4()),
            "occurred_at": "2026-09-19T00:00:00Z",
            "visibility_scope": "PRIVATE",
            "producer": "w4",
            "job_id": str(job.id),
            "company_id": None,
            "question_version_id": str(question_version.id),
            "source_id": str(source.id),
            "analysis_input_version": job.analysis_input_version,
            "decision_scope": "QUESTION_MATCHING",
            "decision_owner": "W4",
            "decision_version": 1,
            "is_core": True,
            "decision_code": "CORE_REQUIRED",
            "reason_code": "QUESTION_EVIDENCE_REQUIRED",
        },
        authenticated_principal="w4-test-sender",
        expected_principal="w4-test-sender",
    )
    assert receipt.decision_id is not None
    decision = session.get(AnalysisSourceDecision, receipt.decision_id)
    binding = session.scalar(
        select(JobCoreDecisionBinding).where(
            JobCoreDecisionBinding.analysis_source_decision_id == receipt.decision_id
        )
    )
    assert decision is not None and binding is not None
    return (
        owner,
        company,
        project,
        question,
        source,
        job,
        question_version,
        source_link,
        decision,
        binding,
    )


def _create_question_core_child(
    *, migrated_engine: Engine, session: Session
) -> tuple[User, Company, Source, Job, JobCommand, OutboxRelay, InMemorySqsPort]:
    (
        owner,
        company,
        _,
        _,
        source,
        job,
        _,
        _,
        _,
        _,
    ) = _seed_accepted_question_core(session)
    retry = session.scalar(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job.id,
            JobRequiredAction.action_code == "RETRY",
            JobRequiredAction.action_status == "OPEN",
        )
    )
    assert retry is not None
    JobService(session).apply_required_action(
        owner_user_id=owner.id,
        job_id=job.id,
        required_action_id=retry.id,
        action_code="RETRY",
        expected_input_version=retry.expected_input_version,
        expected_result_version=retry.expected_result_version,
        acknowledge_rate_limit=False,
    )
    session.commit()
    factory = sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    sqs = InMemorySqsPort()
    relay = OutboxRelay(
        session_factory=factory,
        sqs=sqs,
        queues=QueueUrlRegistry(
            w1_execution_queue_url=EXECUTION_QUEUE_URL,
            w2_collection_command_queue_url=W2_COMMAND_QUEUE_URL,
        ),
        relay_id="question-core-relay",
    )
    assert relay.drain_once(limit=10).published == 1
    sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=sqs.sent_messages[0].body)
    assert JobWorker(
        session_factory=factory,
        sqs=sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="question-core-worker",
    ).drain_once(max_messages=1).acknowledged == 1
    session.expire_all()
    child = session.scalar(
        select(JobCommand).where(
            JobCommand.job_id == job.id,
            JobCommand.command_type == "W2_SOURCE_COLLECTION",
        )
    )
    assert child is not None
    return owner, company, source, job, child, relay, sqs


@pytest.mark.postgres
def test_question_core_resolution_derives_w2_company_without_mutating_the_null_pin(
    db_session: Session,
) -> None:
    (
        owner,
        company,
        source,
        _,
        _,
        job,
        _,
        source_link,
        decision,
        binding,
    ) = _seed_accepted_question_core(db_session)
    pin = project_core_decision_pin(binding=binding, decision=decision)

    resolved = resolve_question_collection_company(
        session=db_session,
        owner=owner,
        job=job,
        decision=decision,
        binding=binding,
        pin=pin,
        expected_command_id=None,
        for_update=True,
    )

    assert pin["company_id"] is None
    assert resolved.company_id == company.id
    assert resolved.source_link_id == source_link.id


@pytest.mark.postgres
def test_w4_explicit_retry_mints_one_new_fence_but_keeps_the_immutable_null_company_pin(
    db_session: Session,
) -> None:
    (
        owner,
        _,
        _,
        _,
        _,
        job,
        _,
        _,
        decision,
        binding,
    ) = _seed_accepted_question_core(db_session)
    retry = db_session.scalar(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job.id,
            JobRequiredAction.action_code == "RETRY",
            JobRequiredAction.action_status == "OPEN",
        )
    )
    assert retry is not None
    original_fence = job.execution_fence

    accepted = JobService(db_session).apply_required_action(
        owner_user_id=owner.id,
        job_id=job.id,
        required_action_id=retry.id,
        action_code="RETRY",
        expected_input_version=retry.expected_input_version,
        expected_result_version=retry.expected_result_version,
        acknowledge_rate_limit=False,
    )
    db_session.flush()

    assert accepted.command is not None and accepted.outbox_message is not None
    assert accepted.job.execution_fence == original_fence + 1
    assert accepted.command.analysis_source_decision_id == decision.id
    pin = accepted.command.payload["core_decision_pin"]
    assert pin["company_id"] is None
    assert pin["question_version_id"] == str(binding.question_version_id)
    assert accepted.outbox_message.payload["core_decision_pin"] == pin


@pytest.mark.postgres
def test_question_core_worker_derives_company_only_for_the_w2_command(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    (
        owner,
        company,
        _,
        _,
        source,
        job,
        _,
        _,
        _,
        _,
    ) = _seed_accepted_question_core(db_session)
    retry = db_session.scalar(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job.id,
            JobRequiredAction.action_code == "RETRY",
            JobRequiredAction.action_status == "OPEN",
        )
    )
    assert retry is not None
    JobService(db_session).apply_required_action(
        owner_user_id=owner.id,
        job_id=job.id,
        required_action_id=retry.id,
        action_code="RETRY",
        expected_input_version=retry.expected_input_version,
        expected_result_version=retry.expected_result_version,
        acknowledge_rate_limit=False,
    )
    db_session.commit()
    factory = sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    sqs = InMemorySqsPort()
    relay = OutboxRelay(
        session_factory=factory,
        sqs=sqs,
        queues=QueueUrlRegistry(
            w1_execution_queue_url=EXECUTION_QUEUE_URL,
            w2_collection_command_queue_url=W2_COMMAND_QUEUE_URL,
        ),
        relay_id="question-core-relay",
    )
    assert relay.drain_once(limit=10).published == 1
    sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=sqs.sent_messages[0].body)
    assert JobWorker(
        session_factory=factory,
        sqs=sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="question-core-worker",
    ).drain_once(max_messages=1).acknowledged == 1

    db_session.expire_all()
    child = db_session.scalar(
        select(JobCommand).where(
            JobCommand.job_id == job.id,
            JobCommand.command_type == "W2_SOURCE_COLLECTION",
        )
    )
    assert child is not None
    assert child.payload["core_decision_pin"]["company_id"] is None
    assert child.payload["w2_command"]["company_id"] == str(company.id)
    available = _lookup_command(
        session=db_session,
        request=LookupRequest(
            schema_version="w1.private.command-lookup.v1",
            command_id=child.id,
            execution_fence=child.execution_fence,
            owner_deletion_epoch=child.owner_deletion_epoch,
        ),
    )
    assert available.status == "AVAILABLE"
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")))
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))
    lookup_engine = create_engine(migrated_engine.url, pool_pre_ping=True)

    @event.listens_for(lookup_engine, "begin")
    def _set_lookup_role(connection) -> None:
        connection.exec_driver_sql("SET LOCAL ROLE epick_lookup")

    lookup_factory = sessionmaker(bind=lookup_engine, autoflush=False, expire_on_commit=False)
    with lookup_factory.begin() as lookup_session:
        lookup_available = _lookup_command(
            session=lookup_session,
            request=LookupRequest(
                schema_version="w1.private.command-lookup.v1",
                command_id=child.id,
                execution_fence=child.execution_fence,
                owner_deletion_epoch=child.owner_deletion_epoch,
            ),
        )
    lookup_engine.dispose()
    assert lookup_available.status == "AVAILABLE"
    assert relay.drain_once(limit=10).published == 1
    assert len(sqs.sent_messages) == 2

    other_company = Company(legal_name="Changed lookup Co", display_name="Changed lookup Co")
    db_session.add(other_company)
    db_session.flush()
    source.company_id = other_company.id
    db_session.flush()
    stale_lookup = _lookup_command(
        session=db_session,
        request=LookupRequest(
            schema_version="w1.private.command-lookup.v1",
            command_id=child.id,
            execution_fence=child.execution_fence,
            owner_deletion_epoch=child.owner_deletion_epoch,
        ),
    )
    assert stale_lookup.status == "EXPIRED"
    assert stale_lookup.reason_code == "COMMAND_BINDING_INVALID"


@pytest.mark.postgres
def test_question_core_relay_blocks_a_child_command_when_current_source_company_changes(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, _, source, _, _, relay, sqs = _create_question_core_child(
        migrated_engine=migrated_engine,
        session=db_session,
    )
    other_company = Company(legal_name="Relay changed Co", display_name="Relay changed Co")
    db_session.add(other_company)
    db_session.flush()
    source.company_id = other_company.id
    db_session.commit()

    result = relay.drain_once(limit=10)

    assert result.failed_final == 1
    assert len(sqs.sent_messages) == 1


@pytest.mark.postgres
@pytest.mark.parametrize(
    "mutation",
    (
        "project_company",
        "question_current_version",
        "source_company",
        "source_link_purpose",
        "analysis_input",
        "owner_deletion_epoch",
    ),
)
def test_question_core_resolution_fails_closed_when_any_current_relation_changes(
    db_session: Session,
    mutation: str,
) -> None:
    (
        owner,
        _,
        project,
        question,
        source,
        job,
        question_version,
        source_link,
        decision,
        binding,
    ) = _seed_accepted_question_core(db_session)
    pin = project_core_decision_pin(binding=binding, decision=decision)

    if mutation == "project_company":
        other_company = Company(legal_name="Other Project Co", display_name="Other Project Co")
        db_session.add(other_company)
        db_session.flush()
        replacement = ApplicationProjectVersion(
            project_id=project.id,
            owner_user_id=owner.id,
            version_no=2,
            company_id=other_company.id,
            title="Changed project",
            role_name="Engineer",
        )
        db_session.add(replacement)
        db_session.flush()
        project.current_version_id = replacement.id
    elif mutation == "question_current_version":
        replacement = QuestionVersion(
            question_id=question.id,
            project_id=project.id,
            owner_user_id=owner.id,
            version_no=2,
            prompt="Changed question",
            source="USER",
        )
        db_session.add(replacement)
        db_session.flush()
        question.current_version_id = replacement.id
    elif mutation == "source_company":
        other_company = Company(legal_name="Other Source Co", display_name="Other Source Co")
        db_session.add(other_company)
        db_session.flush()
        source.company_id = other_company.id
    elif mutation == "source_link_purpose":
        source_link.purpose_ref = "OTHER_PURPOSE"
    elif mutation == "analysis_input":
        job.analysis_input_version = "question-input:changed"
    elif mutation == "owner_deletion_epoch":
        owner.deletion_epoch += 1
    db_session.flush()

    with pytest.raises(QuestionCoreBindingError, match="QUESTION_CORE_BINDING_INVALID"):
        resolve_question_collection_company(
            session=db_session,
            owner=owner,
            job=job,
            decision=decision,
            binding=binding,
            pin=pin,
            expected_command_id=None,
            for_update=True,
        )
