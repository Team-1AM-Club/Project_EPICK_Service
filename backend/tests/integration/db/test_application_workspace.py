from __future__ import annotations

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.application_workspace import ApplicationProjectVersion, QuestionVersion
from app.models.identity import User
from app.services.application_workspace import ApplicationWorkspaceService


@pytest.fixture(autouse=True)
def clean_workspace_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


def _create_owner(db_session: Session, display_name: str) -> User:
    user = User(display_name=display_name, locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(user)
    db_session.flush()
    return user


def test_project_and_question_updates_append_versions_and_advance_only_own_pointers(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Workspace owner")
    service = ApplicationWorkspaceService(db_session)
    company = service.create_company(legal_name="EPICK", display_name="EPICK")
    project = service.create_project(
        owner_user_id=owner.id,
        company_id=company.id,
        title="Backend application",
        role_name="Backend engineer",
    )
    question = service.create_question(
        owner_user_id=owner.id,
        project_id=project.id,
        display_order=0,
        prompt="Why this role?",
        source="USER",
        character_limit=800,
    )
    db_session.commit()

    original_project_version_id = project.current_version_id
    original_question_version_id = question.current_version_id
    appended_project = service.append_project_version(
        owner_user_id=owner.id,
        project_id=project.id,
        expected_lock_version=project.lock_version,
        title="Updated backend application",
        change_reason="User corrected the title",
    )
    appended_question = service.append_question_version(
        owner_user_id=owner.id,
        question_id=question.id,
        expected_lock_version=question.lock_version,
        prompt="Why are you a fit for this backend role?",
    )
    db_session.commit()

    project_versions = db_session.scalars(
        select(ApplicationProjectVersion)
        .where(ApplicationProjectVersion.project_id == project.id)
        .order_by(ApplicationProjectVersion.version_no)
    ).all()
    question_versions = db_session.scalars(
        select(QuestionVersion)
        .where(QuestionVersion.question_id == question.id)
        .order_by(QuestionVersion.version_no)
    ).all()
    db_session.refresh(project)
    db_session.refresh(question)

    assert [version.version_no for version in project_versions] == [1, 2]
    assert project_versions[0].title == "Backend application"
    assert appended_project.title == "Updated backend application"
    assert project.current_version_id == appended_project.id
    assert project.current_version_id != original_project_version_id
    assert [version.version_no for version in question_versions] == [1, 2]
    assert question_versions[0].prompt == "Why this role?"
    assert appended_question.prompt == "Why are you a fit for this backend role?"
    assert question.current_version_id == appended_question.id
    assert question.current_version_id != original_question_version_id


def test_cross_owner_project_version_link_is_rejected_at_database_boundary(
    db_session: Session,
) -> None:
    owner_a = _create_owner(db_session, "Owner A")
    owner_b = _create_owner(db_session, "Owner B")
    service = ApplicationWorkspaceService(db_session)
    company = service.create_company(legal_name="EPICK", display_name="EPICK")
    project_b = service.create_project(
        owner_user_id=owner_b.id,
        company_id=company.id,
        title="Owner B application",
        role_name="Backend engineer",
    )
    db_session.flush()

    invalid_version = ApplicationProjectVersion(
        project_id=project_b.id,
        owner_user_id=owner_a.id,
        version_no=1,
        company_id=company.id,
        title="Invalid cross-owner version",
        role_name="Backend engineer",
    )
    db_session.add(invalid_version)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
