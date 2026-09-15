from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.experience import Episode, EpisodeVersion
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
