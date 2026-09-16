from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    Company,
    ProjectQuestion,
    QuestionVersion,
)
from app.models.experience import Activity
from app.models.identity import User
from app.models.job_postings import JobPosting, JobPostingVersion
from app.models.jobs import Job
from app.models.lifecycle_operations import Notification
from app.models.recommendations import MaterialSelectionSet


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

    def get_project(self, *, project_id: UUID, owner_user_id: UUID) -> ApplicationProject | None:
        return self.session.scalar(
            select(ApplicationProject).where(
                ApplicationProject.id == project_id,
                ApplicationProject.owner_user_id == owner_user_id,
            )
        )

    def get_current_project_version(
        self, *, project: ApplicationProject, owner_user_id: UUID
    ) -> ApplicationProjectVersion | None:
        if project.current_version_id is None:
            return None
        return self.get_project_version(
            project_version_id=project.current_version_id,
            owner_user_id=owner_user_id,
        )

    def list_projects(
        self, *, owner_user_id: UUID, offset: int, limit: int
    ) -> list[tuple[ApplicationProject, ApplicationProjectVersion, bool]]:
        paused_job_exists = exists(
            select(Job.id).where(
                Job.owner_user_id == ApplicationProject.owner_user_id,
                Job.project_id == ApplicationProject.id,
                Job.status.in_(("WAITING_USER", "PAUSED_RATE_LIMIT")),
            )
        )
        statement = (
            select(
                ApplicationProject,
                ApplicationProjectVersion,
                paused_job_exists.label("is_paused"),
            )
            .join(
                ApplicationProjectVersion,
                ApplicationProjectVersion.id == ApplicationProject.current_version_id,
            )
            .where(ApplicationProject.owner_user_id == owner_user_id)
            .order_by(ApplicationProject.updated_at.desc(), ApplicationProject.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return [
            (project, version, is_paused)
            for project, version, is_paused in self.session.execute(statement).all()
        ]

    def list_project_versions(
        self, *, project_id: UUID, owner_user_id: UUID
    ) -> list[ApplicationProjectVersion]:
        return list(
            self.session.scalars(
                select(ApplicationProjectVersion)
                .where(
                    ApplicationProjectVersion.project_id == project_id,
                    ApplicationProjectVersion.owner_user_id == owner_user_id,
                )
                .order_by(ApplicationProjectVersion.version_no)
            )
        )

    def has_paused_job(self, *, project_id: UUID, owner_user_id: UUID) -> bool:
        return bool(
            self.session.scalar(
                select(
                    exists().where(
                        Job.project_id == project_id,
                        Job.owner_user_id == owner_user_id,
                        Job.status.in_(("WAITING_USER", "PAUSED_RATE_LIMIT")),
                    )
                )
            )
        )

    def get_project_version_by_number(
        self, *, project_id: UUID, version_no: int, owner_user_id: UUID
    ) -> ApplicationProjectVersion | None:
        return self.session.scalar(
            select(ApplicationProjectVersion).where(
                ApplicationProjectVersion.project_id == project_id,
                ApplicationProjectVersion.version_no == version_no,
                ApplicationProjectVersion.owner_user_id == owner_user_id,
            )
        )

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

    def get_question(self, *, question_id: UUID, owner_user_id: UUID) -> ProjectQuestion | None:
        return self.session.scalar(
            select(ProjectQuestion).where(
                ProjectQuestion.id == question_id,
                ProjectQuestion.owner_user_id == owner_user_id,
            )
        )

    def get_current_question_version(
        self, *, question: ProjectQuestion, owner_user_id: UUID
    ) -> QuestionVersion | None:
        if question.current_version_id is None:
            return None
        return self.get_question_version(
            question_version_id=question.current_version_id,
            owner_user_id=owner_user_id,
        )

    def list_questions(
        self, *, project_id: UUID, owner_user_id: UUID
    ) -> list[tuple[ProjectQuestion, QuestionVersion]]:
        statement = (
            select(ProjectQuestion, QuestionVersion)
            .join(QuestionVersion, QuestionVersion.id == ProjectQuestion.current_version_id)
            .where(
                ProjectQuestion.project_id == project_id,
                ProjectQuestion.owner_user_id == owner_user_id,
                ProjectQuestion.status == "ACTIVE",
            )
            .order_by(ProjectQuestion.display_order, ProjectQuestion.id)
        )
        return list(self.session.execute(statement).all())

    def list_question_versions(
        self, *, question_id: UUID, owner_user_id: UUID
    ) -> list[QuestionVersion]:
        return list(
            self.session.scalars(
                select(QuestionVersion)
                .where(
                    QuestionVersion.question_id == question_id,
                    QuestionVersion.owner_user_id == owner_user_id,
                )
                .order_by(QuestionVersion.version_no)
            )
        )

    def get_question_version_by_number(
        self, *, question_id: UUID, version_no: int, owner_user_id: UUID
    ) -> QuestionVersion | None:
        return self.session.scalar(
            select(QuestionVersion).where(
                QuestionVersion.question_id == question_id,
                QuestionVersion.version_no == version_no,
                QuestionVersion.owner_user_id == owner_user_id,
            )
        )

    def get_question_by_display_order(
        self, *, project_id: UUID, display_order: int
    ) -> ProjectQuestion | None:
        return self.session.scalar(
            select(ProjectQuestion).where(
                ProjectQuestion.project_id == project_id,
                ProjectQuestion.display_order == display_order,
            )
        )

    def next_question_display_order(self, *, project_id: UUID) -> int:
        return (
            int(
                self.session.scalar(
                    select(func.coalesce(func.max(ProjectQuestion.display_order), -1)).where(
                        ProjectQuestion.project_id == project_id
                    )
                )
                or 0
            )
            + 1
        )

    def has_current_material_selection(self, *, question_id: UUID, owner_user_id: UUID) -> bool:
        return bool(
            self.session.scalar(
                select(
                    exists().where(
                        MaterialSelectionSet.question_id == question_id,
                        MaterialSelectionSet.owner_user_id == owner_user_id,
                        MaterialSelectionSet.is_current.is_(True),
                    )
                )
            )
        )

    def get_company(self, *, company_id: UUID) -> Company | None:
        return self.session.get(Company, company_id)

    def list_companies(self, *, query: str | None, offset: int, limit: int) -> list[Company]:
        statement = select(Company).where(Company.identification_status != "REJECTED")
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(
                    Company.display_name.ilike(pattern),
                    Company.legal_name.ilike(pattern),
                    Company.official_domain.ilike(pattern),
                )
            )
        return list(
            self.session.scalars(
                statement.order_by(Company.display_name, Company.id).offset(offset).limit(limit)
            )
        )

    def get_job_posting(
        self, *, job_posting_id: UUID, company_id: UUID | None = None
    ) -> JobPosting | None:
        statement = select(JobPosting).where(JobPosting.id == job_posting_id)
        if company_id is not None:
            statement = statement.where(JobPosting.company_id == company_id)
        return self.session.scalar(statement)

    def list_current_job_postings(
        self, *, company_id: UUID, offset: int, limit: int
    ) -> list[tuple[JobPosting, JobPostingVersion]]:
        statement = (
            select(JobPosting, JobPostingVersion)
            .join(JobPostingVersion, JobPostingVersion.id == JobPosting.current_version_id)
            .where(JobPosting.company_id == company_id, JobPosting.status != "ARCHIVED")
            .order_by(JobPostingVersion.published_at.desc().nullslast(), JobPosting.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.execute(statement).all())

    def get_user(self, *, owner_user_id: UUID) -> User | None:
        return self.session.get(User, owner_user_id)

    def home_counts(self, *, owner_user_id: UUID) -> dict[str, int]:
        activity_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(Activity)
                .where(Activity.owner_user_id == owner_user_id)
            )
            or 0
        )
        draft_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(Activity)
                .where(
                    Activity.owner_user_id == owner_user_id,
                    Activity.registration_status == "DRAFT",
                )
            )
            or 0
        )
        in_progress_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(ApplicationProject)
                .where(
                    ApplicationProject.owner_user_id == owner_user_id,
                    ApplicationProject.status.in_(("COLLECTING", "READY", "RECOMMENDING")),
                )
            )
            or 0
        )
        needs_review_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(ApplicationProject)
                .where(
                    ApplicationProject.owner_user_id == owner_user_id,
                    ApplicationProject.status == "STALE",
                )
            )
            or 0
        )
        unread_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.owner_user_id == owner_user_id,
                    Notification.read_at.is_(None),
                )
            )
            or 0
        )
        critical_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.owner_user_id == owner_user_id,
                    Notification.read_at.is_(None),
                    Notification.severity == "ERROR",
                )
            )
            or 0
        )
        running_job_count = int(
            self.session.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.owner_user_id == owner_user_id,
                    Job.status.in_(("QUEUED", "RUNNING", "CANCEL_REQUESTED")),
                )
            )
            or 0
        )
        return {
            "activity_count": activity_count,
            "draft_count": draft_count,
            "in_progress_count": in_progress_count,
            "needs_review_count": needs_review_count,
            "unread_count": unread_count,
            "critical_count": critical_count,
            "running_job_count": running_job_count,
        }

    def get_question_version(
        self, *, question_version_id: UUID, owner_user_id: UUID
    ) -> QuestionVersion | None:
        statement = select(QuestionVersion).where(
            QuestionVersion.id == question_version_id,
            QuestionVersion.owner_user_id == owner_user_id,
        )
        return self.session.scalar(statement)
