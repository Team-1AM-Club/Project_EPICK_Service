from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.application_workspace import (
    ApplicationProject,
    Company,
    ProjectQuestion,
    QuestionVersion,
)
from app.models.identity import User
from app.models.jobs import Job, JobCoreDecisionBinding, JobRequiredAction
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source
from app.services.core_decision_inbound import CoreDecisionInboundService


def _seed_w3_binding(session: Session) -> tuple[User, Source, Job]:
    owner = User(display_name="W4 migration owner", locale="ko-KR", timezone="Asia/Seoul")
    company = Company(legal_name="W4 Migration Co", display_name="W4 Migration Co")
    session.add_all((owner, company))
    session.flush()
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url="https://example.test/w4-migration",
        canonical_url_hash=f"w4-migration-{uuid4()}",
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
        analysis_input_version="knowledge-input:w4-migration",
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
    CoreDecisionInboundService(session).apply(
        body={
            "schema_version": "w3.private.core-decision/0.1-candidate",
            "message_type": "w3.private.w1.core-decision",
            "message_id": str(uuid4()),
            "occurred_at": "2026-09-19T00:00:00Z",
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
        },
        authenticated_principal="w3",
    )
    session.flush()
    return owner, source, job


@pytest.mark.postgres
def test_028_backfills_w3_rows_and_keeps_w3_w4_revision_namespaces_distinct(
    migrated_engine: Engine, alembic_config: Config
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE inbox_receipts"))
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))

    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with factory.begin() as session:
        owner, source, job = _seed_w3_binding(session)

    command.downgrade(alembic_config, "027_w2_staged_result_adoption")
    command.upgrade(alembic_config, "head")

    with factory.begin() as session:
        w3_binding = session.scalar(
            select(JobCoreDecisionBinding).where(JobCoreDecisionBinding.job_id == job.id)
        )
        assert w3_binding is not None
        assert w3_binding.origin_producer == "w3"
        assert w3_binding.decision_scope == "COMPANY_KNOWLEDGE"
        assert w3_binding.question_version_id is None
        assert w3_binding.origin_decision_id is None

        project = ApplicationProject(owner_user_id=owner.id)
        session.add(project)
        session.flush()
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
            prompt="Synthetic migration question",
            source="USER",
        )
        session.add(question_version)
        session.flush()
        w4_decision = AnalysisSourceDecision(
            decision_scope="QUESTION_MATCHING",
            company_id=None,
            question_version_id=question_version.id,
            source_id=source.id,
            source_version_id=None,
            analysis_input_version=job.analysis_input_version,
            decision_version=1,
            decision_code="CORE_REQUIRED",
            decision_owner="W4",
            reason_code="QUESTION_EVIDENCE_REQUIRED",
        )
        session.add(w4_decision)
        session.flush()
        session.add(
            JobCoreDecisionBinding(
                job_id=job.id,
                owner_user_id=owner.id,
                source_id=source.id,
                analysis_source_decision_id=w4_decision.id,
                origin_producer="w4",
                decision_scope="QUESTION_MATCHING",
                question_version_id=question_version.id,
                origin_message_id=uuid4(),
                origin_decision_id=uuid4(),
                payload_digest="sha256:" + "4" * 64,
                analysis_input_version=job.analysis_input_version,
                decision_version=1,
                decision_code="CORE_REQUIRED",
                owner_deletion_epoch=0,
            )
        )
        session.flush()

        assert session.scalar(
            select(JobCoreDecisionBinding).where(
                JobCoreDecisionBinding.origin_producer == "w3"
            )
        ) is not None
        assert session.scalar(
            select(JobCoreDecisionBinding).where(
                JobCoreDecisionBinding.origin_producer == "w4"
            )
        ) is not None
