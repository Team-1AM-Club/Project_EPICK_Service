from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    Company,
    ProjectQuestion,
    QuestionVersion,
)
from app.models.identity import User
from app.models.jobs import Job, JobRequiredAction
from app.models.sources import JobSourceLink, Source


def seed_question_job(
    session: Session,
) -> tuple[User, Company, Source, Job, QuestionVersion, JobRequiredAction]:
    """Create one synthetic W4-ready Job and its current private relations."""

    owner = User(display_name="W4 inbound owner", locale="ko-KR", timezone="Asia/Seoul")
    company = Company(legal_name="W4 Inbound Co", display_name="W4 Inbound Co")
    session.add_all((owner, company))
    session.flush()
    project = ApplicationProject(owner_user_id=owner.id)
    session.add(project)
    session.flush()
    project_version = ApplicationProjectVersion(
        project_id=project.id,
        owner_user_id=owner.id,
        version_no=1,
        company_id=company.id,
        title="Synthetic W4 project",
        role_name="Engineer",
    )
    session.add(project_version)
    session.flush()
    project.current_version_id = project_version.id
    question = ProjectQuestion(
        owner_user_id=owner.id,
        project_id=project.id,
        display_order=0,
    )
    session.add(question)
    session.flush()
    question_version = QuestionVersion(
        question_id=question.id,
        project_id=project.id,
        owner_user_id=owner.id,
        version_no=1,
        prompt="Explain a relevant project.",
        source="USER",
    )
    session.add(question_version)
    session.flush()
    question.current_version_id = question_version.id
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url="https://example.test/w4-inbound",
        canonical_url_hash=f"w4-inbound-{uuid4()}",
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
    session.add_all(
        (
            JobSourceLink(
                job_id=job.id,
                owner_user_id=owner.id,
                source_id=source.id,
                source_version_id=None,
                command_id=None,
                purpose_ref="SOURCE_COLLECTION",
                analysis_input_version=job.analysis_input_version,
            ),
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
    action = session.scalar(
        select(JobRequiredAction).where(JobRequiredAction.job_id == job.id)
    )
    assert action is not None
    return owner, company, source, job, question_version, action


def question_core_event(
    *, job: Job, source: Source, question_version: QuestionVersion, is_core: bool = True
) -> dict[str, object]:
    return {
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
        "is_core": is_core,
        "decision_code": "CORE_REQUIRED" if is_core else "NON_CORE_OPTIONAL",
        "reason_code": "QUESTION_EVIDENCE_REQUIRED" if is_core else "SUPPLEMENTARY_CONTEXT",
    }


def reopen_question_core_action(session: Session, *, job: Job) -> None:
    """Model a new W1-owned Core-decision requirement for revision comparisons only."""

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
