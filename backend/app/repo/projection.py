from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.experience import EpisodeVersion
from app.models.jobs import InboxReceipt, OutboxMessage
from app.models.projection import ExperienceExclusion, ProjectionSyncState, SnapshotExclusion


class ProjectionRepository:
    """Persistence primitives for generic projection readiness and fixed exclusions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_projection_state(self, state: ProjectionSyncState) -> None:
        self.session.add(state)

    def get_projection_state_for_update(
        self,
        *,
        resource_type: str,
        resource_id: UUID,
        resource_version: UUID,
        projection_type: str,
    ) -> ProjectionSyncState | None:
        return self.session.scalar(
            select(ProjectionSyncState)
            .where(
                ProjectionSyncState.resource_type == resource_type,
                ProjectionSyncState.resource_id == resource_id,
                ProjectionSyncState.resource_version == resource_version,
                ProjectionSyncState.projection_type == projection_type,
            )
            .with_for_update()
        )

    def add_outbox_message(self, message: OutboxMessage) -> None:
        self.session.add(message)

    def get_public_outbox_message(
        self,
        *,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int,
        message_type: str,
    ) -> OutboxMessage | None:
        return self.session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.visibility_scope == "PUBLIC",
                OutboxMessage.aggregate_type == aggregate_type,
                OutboxMessage.aggregate_id == aggregate_id,
                OutboxMessage.aggregate_revision == aggregate_revision,
                OutboxMessage.message_type == message_type,
            )
        )

    def claim_pending_public_messages(self, *, limit: int) -> list[OutboxMessage]:
        statement = (
            select(OutboxMessage)
            .where(
                OutboxMessage.visibility_scope == "PUBLIC",
                OutboxMessage.status.in_(("PENDING", "FAILED_RETRYABLE")),
                # available_at is written with PostgreSQL's server clock.  Compare it
                # against that same clock so a small application/DB clock skew cannot
                # defer an event that was just committed as immediately available.
                OutboxMessage.available_at <= func.now(),
            )
            .order_by(OutboxMessage.available_at, OutboxMessage.created_at, OutboxMessage.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(self.session.scalars(statement))

    def get_public_outbox_message_for_update(self, *, event_id: UUID) -> OutboxMessage | None:
        return self.session.scalar(
            select(OutboxMessage)
            .where(OutboxMessage.id == event_id, OutboxMessage.visibility_scope == "PUBLIC")
            .with_for_update()
        )

    def record_inbox_receipt(
        self, *, consumer_name: str, event_id: UUID, outcome_code: str
    ) -> bool:
        statement = (
            insert(InboxReceipt)
            .values(
                consumer_name=consumer_name,
                event_id=event_id,
                outcome_code=outcome_code,
            )
            .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
            .returning(InboxReceipt.event_id)
        )
        return self.session.execute(statement).scalar_one_or_none() is not None

    def add_exclusion(self, exclusion: ExperienceExclusion) -> None:
        self.session.add(exclusion)

    def get_exclusion_for_update(
        self, *, exclusion_id: UUID, owner_user_id: UUID
    ) -> ExperienceExclusion | None:
        return self.session.scalar(
            select(ExperienceExclusion)
            .where(
                ExperienceExclusion.id == exclusion_id,
                ExperienceExclusion.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def list_exclusions(
        self, *, owner_user_id: UUID, include_revoked: bool = False
    ) -> list[ExperienceExclusion]:
        statement = select(ExperienceExclusion).where(
            ExperienceExclusion.owner_user_id == owner_user_id
        )
        if not include_revoked:
            statement = statement.where(ExperienceExclusion.revoked_at.is_(None))
        return list(
            self.session.scalars(
                statement.order_by(
                    ExperienceExclusion.created_at.desc(), ExperienceExclusion.id.desc()
                )
            )
        )

    def get_active_exclusions_for_episode_versions(
        self, *, owner_user_id: UUID, episode_version_ids: Sequence[UUID]
    ) -> list[ExperienceExclusion]:
        if not episode_version_ids:
            return []
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
            )
            .distinct()
        )
        return list(self.session.scalars(statement))

    def add_snapshot_exclusion(self, snapshot_exclusion: SnapshotExclusion) -> None:
        self.session.add(snapshot_exclusion)
