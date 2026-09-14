from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    Company,
    ProjectQuestion,
    QuestionVersion,
)


class ApplicationWorkspaceRepository:
    """Owner-scoped persistence primitives for immutable application-work versions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_company(self, company: Company) -> None:
        self.session.add(company)

    def add_project(self, project: ApplicationProject) -> None:
        self.session.add(project)

    def add_project_version(self, version: ApplicationProjectVersion) -> None:
        self.session.add(version)

    def add_question(self, question: ProjectQuestion) -> None:
        self.session.add(question)

    def add_question_version(self, version: QuestionVersion) -> None:
        self.session.add(version)

    def get_project_for_update(
        self, *, project_id: UUID, owner_user_id: UUID
    ) -> ApplicationProject | None:
        statement = (
            select(ApplicationProject)
            .where(
                ApplicationProject.id == project_id,
                ApplicationProject.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )
        return self.session.scalar(statement)

    def get_project_version(
        self, *, project_version_id: UUID, owner_user_id: UUID
    ) -> ApplicationProjectVersion | None:
        statement = select(ApplicationProjectVersion).where(
            ApplicationProjectVersion.id == project_version_id,
            ApplicationProjectVersion.owner_user_id == owner_user_id,
        )
        return self.session.scalar(statement)

    def get_question_for_update(
        self, *, question_id: UUID, owner_user_id: UUID
    ) -> ProjectQuestion | None:
        statement = (
            select(ProjectQuestion)
            .where(
                ProjectQuestion.id == question_id, ProjectQuestion.owner_user_id == owner_user_id
            )
            .with_for_update()
        )
        return self.session.scalar(statement)

    def get_question_version(
        self, *, question_version_id: UUID, owner_user_id: UUID
    ) -> QuestionVersion | None:
        statement = select(QuestionVersion).where(
            QuestionVersion.id == question_version_id,
            QuestionVersion.owner_user_id == owner_user_id,
        )
        return self.session.scalar(statement)
