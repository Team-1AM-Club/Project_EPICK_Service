from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.experience import Activity, Episode, EpisodeVersion
from app.models.lifecycle_operations import (
    ExperienceDuplicateDecision,
    ExperienceDuplicateSuggestion,
    ExperienceMergeRecord,
    InferenceDecision,
    InferenceSuggestion,
    InferenceSuggestionSource,
    JobCheckpoint,
    Notification,
)


class LifecycleOperationsRepository:
    """Owner-scoped persistence primitives for approval-gated P2 operations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_inference_suggestion(self, suggestion: InferenceSuggestion) -> None:
        self.session.add(suggestion)

    def add_inference_source(self, source: InferenceSuggestionSource) -> None:
        self.session.add(source)

    def add_inference_decision(self, decision: InferenceDecision) -> None:
        self.session.add(decision)

    def add_duplicate_suggestion(self, suggestion: ExperienceDuplicateSuggestion) -> None:
        self.session.add(suggestion)

    def add_duplicate_decision(self, decision: ExperienceDuplicateDecision) -> None:
        self.session.add(decision)

    def add_merge_record(self, record: ExperienceMergeRecord) -> None:
        self.session.add(record)

    def add_checkpoint(self, checkpoint: JobCheckpoint) -> None:
        self.session.add(checkpoint)

    def add_notification(self, notification: Notification) -> None:
        self.session.add(notification)

    def get_episode_version(
        self, *, episode_id: UUID, episode_version_id: UUID, owner_user_id: UUID
    ) -> EpisodeVersion | None:
        return self.session.scalar(
            select(EpisodeVersion).where(
                EpisodeVersion.id == episode_version_id,
                EpisodeVersion.episode_id == episode_id,
                EpisodeVersion.owner_user_id == owner_user_id,
            )
        )

    def get_episode_for_update(self, *, episode_id: UUID, owner_user_id: UUID) -> Episode | None:
        return self.session.scalar(
            select(Episode)
            .where(Episode.id == episode_id, Episode.owner_user_id == owner_user_id)
            .with_for_update()
        )

    def get_inference_suggestion_for_update(
        self, *, suggestion_id: UUID, owner_user_id: UUID
    ) -> InferenceSuggestion | None:
        return self.session.scalar(
            select(InferenceSuggestion)
            .where(
                InferenceSuggestion.id == suggestion_id,
                InferenceSuggestion.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_episode(self, *, episode_id: UUID, owner_user_id: UUID) -> Episode | None:
        return self.session.scalar(
            select(Episode).where(Episode.id == episode_id, Episode.owner_user_id == owner_user_id)
        )

    def get_activity(self, *, activity_id: UUID, owner_user_id: UUID) -> Activity | None:
        return self.session.scalar(
            select(Activity).where(
                Activity.id == activity_id, Activity.owner_user_id == owner_user_id
            )
        )

    def list_inference_suggestions(
        self, *, episode_id: UUID, owner_user_id: UUID, offset: int, limit: int
    ) -> list[InferenceSuggestion]:
        return list(
            self.session.scalars(
                select(InferenceSuggestion)
                .where(
                    InferenceSuggestion.episode_id == episode_id,
                    InferenceSuggestion.owner_user_id == owner_user_id,
                )
                .order_by(InferenceSuggestion.created_at.desc(), InferenceSuggestion.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )

    def list_inference_sources(
        self, *, suggestion_id: UUID, owner_user_id: UUID
    ) -> list[InferenceSuggestionSource]:
        return list(
            self.session.scalars(
                select(InferenceSuggestionSource)
                .where(
                    InferenceSuggestionSource.suggestion_id == suggestion_id,
                    InferenceSuggestionSource.owner_user_id == owner_user_id,
                )
                .order_by(InferenceSuggestionSource.created_at, InferenceSuggestionSource.id)
            )
        )

    def get_latest_inference_decision(
        self, *, suggestion_id: UUID, owner_user_id: UUID
    ) -> InferenceDecision | None:
        return self.session.scalar(
            select(InferenceDecision)
            .where(
                InferenceDecision.suggestion_id == suggestion_id,
                InferenceDecision.owner_user_id == owner_user_id,
            )
            .order_by(InferenceDecision.decision_no.desc())
            .limit(1)
        )

    def next_inference_decision_no(self, *, suggestion_id: UUID) -> int:
        last = self.session.scalar(
            select(func.max(InferenceDecision.decision_no)).where(
                InferenceDecision.suggestion_id == suggestion_id
            )
        )
        return int(last or 0) + 1

    def get_duplicate_suggestion_for_update(
        self, *, suggestion_id: UUID, owner_user_id: UUID
    ) -> ExperienceDuplicateSuggestion | None:
        return self.session.scalar(
            select(ExperienceDuplicateSuggestion)
            .where(
                ExperienceDuplicateSuggestion.id == suggestion_id,
                ExperienceDuplicateSuggestion.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def list_duplicate_suggestions(
        self,
        *,
        owner_user_id: UUID,
        activity_id: UUID | None,
        episode_id: UUID | None,
        offset: int,
        limit: int,
    ) -> list[ExperienceDuplicateSuggestion]:
        statement = (
            select(ExperienceDuplicateSuggestion)
            .join(Episode, Episode.id == ExperienceDuplicateSuggestion.left_episode_id)
            .where(
                ExperienceDuplicateSuggestion.owner_user_id == owner_user_id,
                Episode.owner_user_id == owner_user_id,
            )
        )
        if activity_id is not None:
            statement = statement.where(Episode.activity_id == activity_id)
        if episode_id is not None:
            statement = statement.where(
                or_(
                    ExperienceDuplicateSuggestion.left_episode_id == episode_id,
                    ExperienceDuplicateSuggestion.right_episode_id == episode_id,
                )
            )
        return list(
            self.session.scalars(
                statement.order_by(
                    ExperienceDuplicateSuggestion.created_at.desc(),
                    ExperienceDuplicateSuggestion.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )

    def get_duplicate_decision_for_update(
        self, *, decision_id: UUID, owner_user_id: UUID
    ) -> ExperienceDuplicateDecision | None:
        return self.session.scalar(
            select(ExperienceDuplicateDecision)
            .where(
                ExperienceDuplicateDecision.id == decision_id,
                ExperienceDuplicateDecision.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_latest_duplicate_decision(
        self, *, suggestion_id: UUID
    ) -> ExperienceDuplicateDecision | None:
        return self.session.scalar(
            select(ExperienceDuplicateDecision)
            .where(ExperienceDuplicateDecision.suggestion_id == suggestion_id)
            .order_by(ExperienceDuplicateDecision.decision_no.desc())
            .limit(1)
        )

    def next_duplicate_decision_no(self, *, suggestion_id: UUID) -> int:
        last = self.session.scalar(
            select(func.max(ExperienceDuplicateDecision.decision_no)).where(
                ExperienceDuplicateDecision.suggestion_id == suggestion_id
            )
        )
        return int(last or 0) + 1

    def get_merge_record_for_decision(self, *, decision_id: UUID) -> ExperienceMergeRecord | None:
        return self.session.scalar(
            select(ExperienceMergeRecord).where(ExperienceMergeRecord.decision_id == decision_id)
        )

    def get_checkpoint_for_update(
        self, *, checkpoint_id: UUID, owner_user_id: UUID
    ) -> JobCheckpoint | None:
        return self.session.scalar(
            select(JobCheckpoint)
            .where(JobCheckpoint.id == checkpoint_id, JobCheckpoint.owner_user_id == owner_user_id)
            .with_for_update()
        )

    def next_checkpoint_revision(self, *, job_id: UUID) -> int:
        last = self.session.scalar(
            select(func.max(JobCheckpoint.checkpoint_revision)).where(
                JobCheckpoint.job_id == job_id
            )
        )
        return int(last or 0) + 1

    def get_notification_for_update(
        self, *, notification_id: UUID, owner_user_id: UUID
    ) -> Notification | None:
        return self.session.scalar(
            select(Notification)
            .where(Notification.id == notification_id, Notification.owner_user_id == owner_user_id)
            .with_for_update()
        )

    def list_notifications(
        self,
        *,
        owner_user_id: UUID,
        unread_only: bool,
        severity: str | None,
        notification_type: str | None,
        offset: int,
        limit: int,
    ) -> list[Notification]:
        statement = select(Notification).where(Notification.owner_user_id == owner_user_id)
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        if severity is not None:
            statement = statement.where(Notification.severity == severity)
        if notification_type is not None:
            statement = statement.where(Notification.notification_type == notification_type)
        statement = (
            statement.order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def get_notification(
        self, *, notification_id: UUID, owner_user_id: UUID
    ) -> Notification | None:
        return self.session.scalar(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.owner_user_id == owner_user_id,
            )
        )
