from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.experience import Activity, ActivityVersion, Episode, EpisodeVersion


class ExperienceRepository:
    """Owner-scoped persistence primitives for immutable Experience versions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_activity(self, activity: Activity) -> None:
        self.session.add(activity)

    def add_activity_version(self, version: ActivityVersion) -> None:
        self.session.add(version)

    def add_episode(self, episode: Episode) -> None:
        self.session.add(episode)

    def add_episode_version(self, version: EpisodeVersion) -> None:
        self.session.add(version)

    def get_activity_for_update(self, *, activity_id: UUID, owner_user_id: UUID) -> Activity | None:
        statement = (
            select(Activity)
            .where(Activity.id == activity_id, Activity.owner_user_id == owner_user_id)
            .with_for_update()
        )
        return self.session.scalar(statement)

    def get_activity_version(
        self, *, activity_version_id: UUID, owner_user_id: UUID
    ) -> ActivityVersion | None:
        statement = select(ActivityVersion).where(
            ActivityVersion.id == activity_version_id,
            ActivityVersion.owner_user_id == owner_user_id,
        )
        return self.session.scalar(statement)

    def get_episode_for_update(self, *, episode_id: UUID, owner_user_id: UUID) -> Episode | None:
        statement = (
            select(Episode)
            .where(Episode.id == episode_id, Episode.owner_user_id == owner_user_id)
            .with_for_update()
        )
        return self.session.scalar(statement)

    def get_episode_version(
        self, *, episode_version_id: UUID, owner_user_id: UUID
    ) -> EpisodeVersion | None:
        statement = select(EpisodeVersion).where(
            EpisodeVersion.id == episode_version_id,
            EpisodeVersion.owner_user_id == owner_user_id,
        )
        return self.session.scalar(statement)
