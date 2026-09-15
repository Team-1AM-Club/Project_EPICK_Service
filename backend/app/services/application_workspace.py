from __future__ import annotations

from typing import Final
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    Company,
    ProjectQuestion,
    QuestionVersion,
)
from app.repo.application_workspace import ApplicationWorkspaceRepository

_UNSET: Final = object()
_PROJECT_COPY_FIELDS: Final = (
    "company_id",
    "title",
    "season",
    "organization_name",
    "role_name",
    "role_version_id",
)
_QUESTION_COPY_FIELDS: Final = ("prompt", "character_limit", "source")


class ApplicationWorkspaceError(Exception):
    pass


class ApplicationWorkspaceNotFoundError(ApplicationWorkspaceError):
    pass


class WorkspaceVersionConflictError(ApplicationWorkspaceError):
    pass


class ApplicationWorkspaceService:
    """Append-only Project and Question version orchestration.

    The request layer owns the transaction and sets the PostgreSQL owner context before calling
    this service.  The service itself never commits a partial Project, Question, or Version.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ApplicationWorkspaceRepository(session)

    def create_company(
        self,
        *,
        legal_name: str,
        display_name: str,
        country_code: str | None = None,
        official_domain: str | None = None,
    ) -> Company:
        company = Company(
            legal_name=legal_name,
            display_name=display_name,
            country_code=country_code,
            official_domain=official_domain,
        )
        self.repository.add_company(company)
        self.session.flush()
        return company

    def create_project(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        title: str,
        role_name: str,
        season: str | None = None,
        organization_name: str | None = None,
    ) -> ApplicationProject:
        self._validate_project_values(title=title, role_name=role_name)
        project = ApplicationProject(owner_user_id=owner_user_id)
        self.repository.add_project(project)
        self.session.flush()
        version = ApplicationProjectVersion(
            project_id=project.id,
            owner_user_id=owner_user_id,
            version_no=1,
            company_id=company_id,
            title=title,
            season=season,
            organization_name=organization_name,
            role_name=role_name,
        )
        self.repository.add_project_version(version)
        self.session.flush()
        project.current_version_id = version.id
        project.updated_at = func.now()
        self.session.flush()
        return project

    def append_project_version(
        self,
        *,
        owner_user_id: UUID,
        project_id: UUID,
        expected_lock_version: int,
        company_id: UUID | object = _UNSET,
        title: str | object = _UNSET,
        role_name: str | object = _UNSET,
        season: str | None | object = _UNSET,
        organization_name: str | None | object = _UNSET,
        change_reason: str | None = None,
    ) -> ApplicationProjectVersion:
        project = self.repository.get_project_for_update(
            project_id=project_id, owner_user_id=owner_user_id
        )
        if project is None or project.current_version_id is None:
            raise ApplicationWorkspaceNotFoundError("project does not exist for this owner")
        if project.lock_version != expected_lock_version:
            raise WorkspaceVersionConflictError("project has a newer immutable version")
        current = self.repository.get_project_version(
            project_version_id=project.current_version_id, owner_user_id=owner_user_id
        )
        if current is None:
            raise ApplicationWorkspaceNotFoundError("current project version does not exist")
        values = {field: getattr(current, field) for field in _PROJECT_COPY_FIELDS}
        values.update(
            self._provided_updates(
                company_id=company_id,
                title=title,
                role_name=role_name,
                season=season,
                organization_name=organization_name,
            )
        )
        self._validate_project_values(title=values["title"], role_name=values["role_name"])
        version = ApplicationProjectVersion(
            project_id=project.id,
            owner_user_id=owner_user_id,
            version_no=current.version_no + 1,
            change_reason=change_reason,
            **values,
        )
        self.repository.add_project_version(version)
        self.session.flush()
        project.current_version_id = version.id
        project.lock_version += 1
        project.updated_at = func.now()
        self.session.flush()
        return version

    def create_question(
        self,
        *,
        owner_user_id: UUID,
        project_id: UUID,
        display_order: int,
        prompt: str,
        source: str,
        character_limit: int | None = None,
    ) -> ProjectQuestion:
        self._validate_question_values(
            prompt=prompt, character_limit=character_limit, source=source
        )
        project = self.repository.get_project_for_update(
            project_id=project_id, owner_user_id=owner_user_id
        )
        if project is None:
            raise ApplicationWorkspaceNotFoundError("project does not exist for this owner")
        question = ProjectQuestion(
            owner_user_id=owner_user_id,
            project_id=project.id,
            display_order=display_order,
        )
        self.repository.add_question(question)
        self.session.flush()
        version = QuestionVersion(
            question_id=question.id,
            project_id=project.id,
            owner_user_id=owner_user_id,
            version_no=1,
            prompt=prompt,
            character_limit=character_limit,
            source=source,
        )
        self.repository.add_question_version(version)
        self.session.flush()
        question.current_version_id = version.id
        question.updated_at = func.now()
        self.session.flush()
        return question

    def append_question_version(
        self,
        *,
        owner_user_id: UUID,
        question_id: UUID,
        expected_lock_version: int,
        prompt: str | object = _UNSET,
        source: str | object = _UNSET,
        character_limit: int | None | object = _UNSET,
    ) -> QuestionVersion:
        question = self.repository.get_question_for_update(
            question_id=question_id, owner_user_id=owner_user_id
        )
        if question is None or question.current_version_id is None:
            raise ApplicationWorkspaceNotFoundError("question does not exist for this owner")
        if question.lock_version != expected_lock_version:
            raise WorkspaceVersionConflictError("question has a newer immutable version")
        current = self.repository.get_question_version(
            question_version_id=question.current_version_id, owner_user_id=owner_user_id
        )
        if current is None:
            raise ApplicationWorkspaceNotFoundError("current question version does not exist")
        values = {field: getattr(current, field) for field in _QUESTION_COPY_FIELDS}
        values.update(
            self._provided_updates(prompt=prompt, source=source, character_limit=character_limit)
        )
        self._validate_question_values(**values)
        version = QuestionVersion(
            question_id=question.id,
            project_id=question.project_id,
            owner_user_id=owner_user_id,
            version_no=current.version_no + 1,
            **values,
        )
        self.repository.add_question_version(version)
        self.session.flush()
        question.current_version_id = version.id
        question.lock_version += 1
        question.updated_at = func.now()
        self.session.flush()
        return version

    @staticmethod
    def _provided_updates(**values: object) -> dict[str, object]:
        return {field: value for field, value in values.items() if value is not _UNSET}

    @staticmethod
    def _validate_project_values(*, title: object, role_name: object) -> None:
        if not isinstance(title, str) or not title.strip():
            raise ApplicationWorkspaceError("project title must be present")
        if not isinstance(role_name, str) or not role_name.strip():
            raise ApplicationWorkspaceError("role name must be present")

    @staticmethod
    def _validate_question_values(
        *, prompt: object, character_limit: object, source: object
    ) -> None:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ApplicationWorkspaceError("question prompt must be present")
        if not isinstance(source, str) or not source.strip():
            raise ApplicationWorkspaceError("question source must be present")
        if character_limit is not None and (
            not isinstance(character_limit, int) or character_limit <= 0
        ):
            raise ApplicationWorkspaceError("question character limit must be positive")
