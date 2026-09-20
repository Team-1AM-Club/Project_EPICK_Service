from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application_workspace import ApplicationProject, ProjectQuestion
from app.models.identity import User
from app.models.recommendation_execution import (
    RecommendationExecutionBinding,
    RecommendationExecutionEpisode,
    RecommendationPublication,
    RecommendationSourceDependency,
)
from app.models.recommendations import (
    ProjectSnapshot,
    RecommendationCandidate,
    RecommendationRun,
    SnapshotEpisodeVersion,
)
from app.models.sources import Source


class RecommendationExecutionRepository:
    """Locked primitives for the W1-owned W4 execution boundary."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_binding(self, value: RecommendationExecutionBinding) -> None:
        self.session.add(value)

    def add_episode(self, value: RecommendationExecutionEpisode) -> None:
        self.session.add(value)

    def add_publication(self, value: RecommendationPublication) -> None:
        self.session.add(value)

    def add_dependency(self, value: RecommendationSourceDependency) -> None:
        self.session.add(value)

    def add_candidate(self, value: RecommendationCandidate) -> None:
        self.session.add(value)

    def lock_owner(self, *, owner_user_id: UUID) -> User | None:
        return self.session.scalar(select(User).where(User.id == owner_user_id).with_for_update())

    def lock_run(self, *, owner_user_id: UUID, run_id: UUID) -> RecommendationRun | None:
        return self.session.scalar(
            select(RecommendationRun)
            .where(
                RecommendationRun.id == run_id,
                RecommendationRun.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def lock_binding(
        self, *, owner_user_id: UUID, run_id: UUID
    ) -> RecommendationExecutionBinding | None:
        return self.session.scalar(
            select(RecommendationExecutionBinding)
            .where(
                RecommendationExecutionBinding.run_id == run_id,
                RecommendationExecutionBinding.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_binding_by_lease(
        self, *, run_id: UUID, lease_token: UUID, for_update: bool = False
    ) -> RecommendationExecutionBinding | None:
        statement = select(RecommendationExecutionBinding).where(
            RecommendationExecutionBinding.run_id == run_id,
            RecommendationExecutionBinding.lease_token == lease_token,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list_episodes(self, *, binding_id: UUID) -> list[RecommendationExecutionEpisode]:
        return list(
            self.session.scalars(
                select(RecommendationExecutionEpisode)
                .where(RecommendationExecutionEpisode.binding_id == binding_id)
                .order_by(
                    RecommendationExecutionEpisode.episode_id,
                    RecommendationExecutionEpisode.episode_version,
                )
            )
        )

    def get_publication(self, *, run_id: UUID) -> RecommendationPublication | None:
        return self.session.scalar(
            select(RecommendationPublication).where(RecommendationPublication.run_id == run_id)
        )

    def run_inputs_are_current(
        self,
        *,
        binding: RecommendationExecutionBinding,
        owner: User,
        run: RecommendationRun,
    ) -> bool:
        if (
            owner.account_status != "ACTIVE"
            or owner.deleted_at is not None
            or owner.deletion_epoch != binding.owner_deletion_epoch
            or run.status != "RUNNING"
            or run.result_status != "PENDING"
            or run.result_origin != "ENGINE"
            or run.question_version_id != binding.question_version_id
            or run.snapshot_id != binding.snapshot_id
        ):
            return False
        question = self.session.scalar(
            select(ProjectQuestion).where(
                ProjectQuestion.id == binding.question_id,
                ProjectQuestion.owner_user_id == binding.owner_user_id,
            )
        )
        project = self.session.scalar(
            select(ApplicationProject).where(
                ApplicationProject.id == binding.project_id,
                ApplicationProject.owner_user_id == binding.owner_user_id,
            )
        )
        snapshot = self.session.scalar(
            select(ProjectSnapshot).where(
                ProjectSnapshot.id == binding.snapshot_id,
                ProjectSnapshot.owner_user_id == binding.owner_user_id,
            )
        )
        if (
            question is None
            or question.status != "ACTIVE"
            or question.current_version_id != binding.question_version_id
            or project is None
            or project.active_snapshot_id != binding.snapshot_id
            or snapshot is None
            or snapshot.status != "READY"
        ):
            return False
        expected = {item.episode_version_id for item in self.list_episodes(binding_id=binding.id)}
        current = set(
            self.session.scalars(
                select(SnapshotEpisodeVersion.episode_version_id).where(
                    SnapshotEpisodeVersion.snapshot_id == binding.snapshot_id,
                    SnapshotEpisodeVersion.owner_user_id == binding.owner_user_id,
                )
            )
        )
        return expected == current

    def source_is_current(self, *, source_id: UUID, source_version_id: UUID) -> bool:
        return (
            self.session.scalar(
                select(Source.id).where(
                    Source.id == source_id,
                    Source.current_version_id == source_version_id,
                )
            )
            is not None
        )
