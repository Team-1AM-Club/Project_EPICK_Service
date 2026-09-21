from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.application_workspace import (
    ApplicationProject,
    ProjectQuestion,
    QuestionVersion,
)
from app.models.jobs import JobCoreDecisionBinding
from app.models.sources import AnalysisSourceDecision


def _seed_w3_binding(session: Session) -> tuple[UUID, UUID, UUID]:
    """Insert a genuine revision-027 W3 row without loading the later ORM mapping."""

    owner_id, company_id, source_id, job_id, decision_id, binding_id = (
        uuid4() for _ in range(6)
    )
    session.execute(
        text("INSERT INTO users (id, display_name, locale, timezone) "
             "VALUES (:id, 'W4 migration owner', 'ko-KR', 'Asia/Seoul')"),
        {"id": owner_id},
    )
    session.execute(
        text("INSERT INTO companies (id, legal_name, display_name) "
             "VALUES (:id, 'W4 Migration Co', 'W4 Migration Co')"),
        {"id": company_id},
    )
    session.execute(
        text(
            "INSERT INTO sources (id, company_id, source_type, canonical_url, "
            "canonical_url_hash, url_normalization_version, policy_version, "
            "policy_checked_at) VALUES (:id, :company_id, 'CAREERS', "
            "'https://example.test/w4-migration', :hash, 'v1', 'policy-v1', now())"
        ),
        {"id": source_id, "company_id": company_id, "hash": f"w4-migration-{source_id}"},
    )
    session.execute(
        text(
            "INSERT INTO jobs (id, owner_user_id, job_type, status, dispatch_status, "
            "owner_deletion_epoch, analysis_input_version) VALUES "
            "(:id, :owner_id, 'SOURCE_COLLECTION', 'WAITING_USER', 'BLOCKED', "
            "0, 'knowledge-input:w4-migration')"
        ),
        {"id": job_id, "owner_id": owner_id},
    )
    session.execute(
        text(
            "INSERT INTO analysis_source_decisions (id, decision_scope, company_id, "
            "source_id, analysis_input_version, decision_version, decision_code, "
            "decision_owner, reason_code) VALUES (:id, 'COMPANY_KNOWLEDGE', "
            ":company_id, :source_id, 'knowledge-input:w4-migration', 1, "
            "'CORE_REQUIRED', 'W3', 'REQUIRED_COMPANY_EVIDENCE')"
        ),
        {"id": decision_id, "company_id": company_id, "source_id": source_id},
    )
    session.execute(
        text(
            "INSERT INTO job_core_decision_bindings (id, job_id, owner_user_id, "
            "source_id, analysis_source_decision_id, origin_message_id, "
            "payload_digest, analysis_input_version, decision_version, decision_code, "
            "owner_deletion_epoch) VALUES (:id, :job_id, :owner_id, :source_id, "
            ":decision_id, :message_id, :digest, 'knowledge-input:w4-migration', "
            "1, 'CORE_REQUIRED', 0)"
        ),
        {
            "id": binding_id,
            "job_id": job_id,
            "owner_id": owner_id,
            "source_id": source_id,
            "decision_id": decision_id,
            "message_id": uuid4(),
            "digest": "sha256:" + "3" * 64,
        },
    )
    return owner_id, source_id, job_id


@pytest.mark.postgres
def test_028_backfills_w3_rows_and_keeps_w3_w4_revision_namespaces_distinct(
    fresh_migration_config: Config,
) -> None:
    command.upgrade(fresh_migration_config, "027_w2_staged_result_adoption")
    engine = create_engine(fresh_migration_config.get_main_option("sqlalchemy.url"))
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory.begin() as session:
            owner_id, source_id, job_id = _seed_w3_binding(session)

        command.upgrade(fresh_migration_config, "head")

        with factory.begin() as session:
            w3_binding = session.scalar(
                select(JobCoreDecisionBinding).where(JobCoreDecisionBinding.job_id == job_id)
            )
            assert w3_binding is not None
            assert w3_binding.origin_producer == "w3"
            assert w3_binding.decision_scope == "COMPANY_KNOWLEDGE"
            assert w3_binding.question_version_id is None
            assert w3_binding.origin_decision_id is None

            project = ApplicationProject(owner_user_id=owner_id)
            session.add(project)
            session.flush()
            question = ProjectQuestion(
                owner_user_id=owner_id,
                project_id=project.id,
                display_order=0,
            )
            session.add(question)
            session.flush()
            question_version = QuestionVersion(
                question_id=question.id,
                project_id=project.id,
                owner_user_id=owner_id,
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
                source_id=source_id,
                source_version_id=None,
                analysis_input_version="knowledge-input:w4-migration",
                decision_version=1,
                decision_code="CORE_REQUIRED",
                decision_owner="W4",
                reason_code="QUESTION_EVIDENCE_REQUIRED",
            )
            session.add(w4_decision)
            session.flush()
            session.add(
                JobCoreDecisionBinding(
                    job_id=job_id,
                    owner_user_id=owner_id,
                    source_id=source_id,
                    analysis_source_decision_id=w4_decision.id,
                    origin_producer="w4",
                    decision_scope="QUESTION_MATCHING",
                    question_version_id=question_version.id,
                    origin_message_id=uuid4(),
                    origin_decision_id=uuid4(),
                    payload_digest="sha256:" + "4" * 64,
                    analysis_input_version="knowledge-input:w4-migration",
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
    finally:
        engine.dispose()
