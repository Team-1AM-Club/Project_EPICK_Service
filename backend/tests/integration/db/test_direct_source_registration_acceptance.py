from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import set_local_owner_context
from app.models.application_workspace import ApplicationProject, ApplicationProjectVersion, Company
from app.models.identity import User
from app.models.jobs import Job, JobInputRef
from app.models.sources import AnalysisSourceDecision, Source
from app.services.jobs import JobError
from app.services.source_collections import SourceCollectionService


@pytest.mark.postgres
def test_acceptance_creates_source_job_decision_and_project_input_atomically(
    db_session: Session,
) -> None:
    owner = User(display_name="Atomic owner", locale="ko-KR", timezone="Asia/Seoul")
    company = Company(
        legal_name="Atomic Corp",
        display_name="Atomic Corp",
        official_domain="atomic.example",
        identification_status="VERIFIED",
    )
    db_session.add_all([owner, company])
    db_session.flush()
    set_local_owner_context(db_session, owner.id)
    project = ApplicationProject(owner_user_id=owner.id)
    db_session.add(project)
    db_session.flush()
    version = ApplicationProjectVersion(
        project_id=project.id,
        owner_user_id=owner.id,
        version_no=1,
        company_id=company.id,
        title="Atomic application",
        role_name="Engineer",
    )
    db_session.add(version)
    db_session.flush()
    project.current_version_id = version.id
    db_session.flush()

    accepted = SourceCollectionService(db_session).accept(
        owner_user_id=owner.id,
        project_id=project.id,
        official_url="https://atomic.example/jobs/7",
        purpose="JOB_POSTING",
        idempotency_key="atomic-source-7",
    )

    assert accepted.job.project_id == project.id
    assert accepted.source.company_id == company.id
    assert accepted.replayed is False
    assert db_session.scalar(
        select(func.count()).select_from(Source).where(Source.company_id == company.id)
    ) == 1
    assert db_session.scalar(
        select(func.count()).select_from(Job).where(Job.owner_user_id == owner.id)
    ) == 1
    assert db_session.scalar(
        select(func.count())
        .select_from(AnalysisSourceDecision)
        .where(AnalysisSourceDecision.company_id == company.id)
    ) == 1
    input_ref = db_session.scalar(select(JobInputRef).where(JobInputRef.job_id == accepted.job.id))
    assert input_ref is not None
    assert input_ref.project_version_id == version.id


@pytest.mark.postgres
def test_acceptance_failure_rolls_back_new_source(migrated_engine: Engine) -> None:
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    owner_id = uuid4()
    company_id = uuid4()
    project_id = uuid4()
    with factory.begin() as setup:
        owner = User(
            id=owner_id, display_name="Rollback owner", locale="ko-KR", timezone="Asia/Seoul"
        )
        company = Company(
            id=company_id,
            legal_name="Rollback Corp",
            display_name="Rollback Corp",
            official_domain="rollback.example",
            identification_status="VERIFIED",
        )
        setup.add_all([owner, company])
        setup.flush()
        project = ApplicationProject(id=project_id, owner_user_id=owner_id)
        setup.add(project)
        setup.flush()
        version = ApplicationProjectVersion(
            project_id=project.id,
            owner_user_id=owner_id,
            version_no=1,
            company_id=company_id,
            title="Rollback application",
            role_name="Engineer",
        )
        setup.add(version)
        setup.flush()
        project.current_version_id = version.id

    failed = factory()
    try:
        failed.begin()
        set_local_owner_context(failed, owner_id)
        with pytest.raises(JobError):
            SourceCollectionService(failed).accept(
                owner_user_id=owner_id,
                project_id=project_id,
                official_url="https://rollback.example/jobs/7",
                purpose="JOB_POSTING",
                idempotency_key="",
            )
        failed.rollback()
    finally:
        failed.close()

    with factory.begin() as verification:
        set_local_owner_context(verification, owner_id)
        assert verification.scalar(
            select(func.count()).select_from(Source).where(Source.company_id == company_id)
        ) == 0
