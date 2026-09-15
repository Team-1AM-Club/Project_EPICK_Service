from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, column, func, or_, select, table
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Session

from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    ProjectQuestion,
    QuestionVersion,
)
from app.models.experience import EpisodeVersion
from app.models.projection import ExperienceExclusion, SnapshotExclusion
from app.models.recommendations import (
    MaterialSelectionItem,
    MaterialSelectionSet,
    ProjectSnapshot,
    RecommendationCandidate,
    RecommendationRun,
    SnapshotEpisodeVersion,
)


class RecommendationRepository:
    """Owner-scoped persistence primitives for immutable recommendation inputs and choices."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_snapshot(self, snapshot: ProjectSnapshot) -> None:
        self.session.add(snapshot)

    def add_snapshot_episode_version(self, item: SnapshotEpisodeVersion) -> None:
        self.session.add(item)

    def add_snapshot_exclusion(self, item: SnapshotExclusion) -> None:
        self.session.add(item)

    def add_run(self, run: RecommendationRun) -> None:
        self.session.add(run)

    def add_candidate(self, candidate: RecommendationCandidate) -> None:
        self.session.add(candidate)

    def add_selection_set(self, selection_set: MaterialSelectionSet) -> None:
        self.session.add(selection_set)

    def add_selection_item(self, item: MaterialSelectionItem) -> None:
        self.session.add(item)

    def get_project_for_update(
        self, *, project_id: UUID, owner_user_id: UUID
    ) -> ApplicationProject | None:
        return self.session.scalar(
            select(ApplicationProject)
            .where(
                ApplicationProject.id == project_id,
                ApplicationProject.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_question_for_update(
        self, *, question_id: UUID, owner_user_id: UUID
    ) -> ProjectQuestion | None:
        return self.session.scalar(
            select(ProjectQuestion)
            .where(
                ProjectQuestion.id == question_id,
                ProjectQuestion.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_question_version(
        self, *, question_version_id: UUID, owner_user_id: UUID
    ) -> QuestionVersion | None:
        return self.session.scalar(
            select(QuestionVersion).where(
                QuestionVersion.id == question_version_id,
                QuestionVersion.owner_user_id == owner_user_id,
            )
        )

    def get_project_version(
        self, *, project_version_id: UUID, project_id: UUID, owner_user_id: UUID
    ) -> ApplicationProjectVersion | None:
        return self.session.scalar(
            select(ApplicationProjectVersion).where(
                ApplicationProjectVersion.id == project_version_id,
                ApplicationProjectVersion.project_id == project_id,
                ApplicationProjectVersion.owner_user_id == owner_user_id,
            )
        )

    def get_role_id_for_role_version(self, *, role_version_id: UUID | None) -> UUID | None:
        if role_version_id is None:
            return None
        role_versions = table(
            "role_versions",
            column("id", PostgreSQLUUID(as_uuid=True)),
            column("role_id", PostgreSQLUUID(as_uuid=True)),
        )
        return self.session.scalar(
            select(role_versions.c.role_id).where(role_versions.c.id == role_version_id)
        )

    def get_snapshot(self, *, snapshot_id: UUID, owner_user_id: UUID) -> ProjectSnapshot | None:
        return self.session.scalar(
            select(ProjectSnapshot).where(
                ProjectSnapshot.id == snapshot_id,
                ProjectSnapshot.owner_user_id == owner_user_id,
            )
        )

    def get_run_for_update(self, *, run_id: UUID, owner_user_id: UUID) -> RecommendationRun | None:
        return self.session.scalar(
            select(RecommendationRun)
            .where(RecommendationRun.id == run_id, RecommendationRun.owner_user_id == owner_user_id)
            .with_for_update()
        )

    def get_episode_versions(
        self, *, episode_version_ids: Sequence[UUID], owner_user_id: UUID
    ) -> list[EpisodeVersion]:
        if not episode_version_ids:
            return []
        return list(
            self.session.scalars(
                select(EpisodeVersion).where(
                    EpisodeVersion.id.in_(episode_version_ids),
                    EpisodeVersion.owner_user_id == owner_user_id,
                )
            )
        )

    def next_snapshot_no(self, *, project_id: UUID) -> int:
        latest = self.session.scalar(
            select(func.coalesce(func.max(ProjectSnapshot.snapshot_no), 0)).where(
                ProjectSnapshot.project_id == project_id
            )
        )
        return int(latest) + 1

    def next_candidate_no(self, *, run_id: UUID) -> int:
        latest = self.session.scalar(
            select(func.coalesce(func.max(RecommendationCandidate.candidate_no), 0)).where(
                RecommendationCandidate.run_id == run_id
            )
        )
        return int(latest) + 1

    def snapshot_contains_episode_version(
        self, *, snapshot_id: UUID, episode_version_id: UUID, owner_user_id: UUID
    ) -> bool:
        return (
            self.session.scalar(
                select(SnapshotEpisodeVersion.snapshot_id).where(
                    SnapshotEpisodeVersion.snapshot_id == snapshot_id,
                    SnapshotEpisodeVersion.episode_version_id == episode_version_id,
                    SnapshotEpisodeVersion.owner_user_id == owner_user_id,
                )
            )
            is not None
        )

    def get_active_exclusions_for_episode_versions(
        self,
        *,
        owner_user_id: UUID,
        episode_version_ids: Sequence[UUID],
        project_id: UUID,
        company_id: UUID,
        role_id: UUID | None,
    ) -> list[ExperienceExclusion]:
        if not episode_version_ids:
            return []
        scope_matches = [
            ExperienceExclusion.scope == "GLOBAL",
            and_(
                ExperienceExclusion.scope == "COMPANY",
                ExperienceExclusion.company_id == company_id,
            ),
            and_(
                ExperienceExclusion.scope == "PROJECT",
                ExperienceExclusion.project_id == project_id,
            ),
        ]
        if role_id is not None:
            scope_matches.append(
                and_(
                    ExperienceExclusion.scope == "ROLE",
                    ExperienceExclusion.role_id == role_id,
                )
            )
        statement = (
            select(ExperienceExclusion)
            .join(
                EpisodeVersion,
                or_(
                    ExperienceExclusion.episode_id == EpisodeVersion.episode_id,
                    ExperienceExclusion.activity_id == EpisodeVersion.activity_id,
                ),
            )
            .where(
                ExperienceExclusion.owner_user_id == owner_user_id,
                ExperienceExclusion.revoked_at.is_(None),
                EpisodeVersion.owner_user_id == owner_user_id,
                EpisodeVersion.id.in_(episode_version_ids),
                or_(*scope_matches),
            )
            .distinct()
        )
        return list(self.session.scalars(statement))

    def get_candidates(
        self,
        *,
        candidate_ids: Sequence[UUID],
        run_id: UUID,
        question_id: UUID,
        owner_user_id: UUID,
    ) -> list[RecommendationCandidate]:
        if not candidate_ids:
            return []
        return list(
            self.session.scalars(
                select(RecommendationCandidate).where(
                    RecommendationCandidate.id.in_(candidate_ids),
                    RecommendationCandidate.run_id == run_id,
                    RecommendationCandidate.question_id == question_id,
                    RecommendationCandidate.owner_user_id == owner_user_id,
                )
            )
        )

    def get_current_selection_for_update(
        self, *, question_id: UUID, owner_user_id: UUID
    ) -> MaterialSelectionSet | None:
        return self.session.scalar(
            select(MaterialSelectionSet)
            .where(
                MaterialSelectionSet.question_id == question_id,
                MaterialSelectionSet.owner_user_id == owner_user_id,
                MaterialSelectionSet.is_current.is_(True),
            )
            .with_for_update()
        )

    def active_question_count(self, *, project_id: UUID, owner_user_id: UUID) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(ProjectQuestion)
                .where(
                    ProjectQuestion.project_id == project_id,
                    ProjectQuestion.owner_user_id == owner_user_id,
                    ProjectQuestion.status == "ACTIVE",
                )
            )
            or 0
        )

    def current_selection_count(self, *, project_id: UUID, owner_user_id: UUID) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(MaterialSelectionSet)
                .join(ProjectQuestion, ProjectQuestion.id == MaterialSelectionSet.question_id)
                .where(
                    ProjectQuestion.project_id == project_id,
                    ProjectQuestion.owner_user_id == owner_user_id,
                    MaterialSelectionSet.owner_user_id == owner_user_id,
                    MaterialSelectionSet.is_current.is_(True),
                )
            )
            or 0
        )
