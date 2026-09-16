from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.experience import (
    Activity,
    ActivityVersion,
    Episode,
    EpisodeVersion,
    EpisodeVersionSkill,
)


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

    def add_episode_version_skill(self, skill: EpisodeVersionSkill) -> None:
        self.session.add(skill)

    def get_activity(self, *, activity_id: UUID, owner_user_id: UUID) -> Activity | None:
        statement = select(Activity).where(
            Activity.id == activity_id,
            Activity.owner_user_id == owner_user_id,
        )
        return self.session.scalar(statement)

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

    def get_activity_version_by_number(
        self, *, activity_id: UUID, version_no: int, owner_user_id: UUID
    ) -> ActivityVersion | None:
        statement = select(ActivityVersion).where(
            ActivityVersion.activity_id == activity_id,
            ActivityVersion.version_no == version_no,
            ActivityVersion.owner_user_id == owner_user_id,
        )
        return self.session.scalar(statement)

    def list_activity_versions(
        self, *, activity_id: UUID, owner_user_id: UUID
    ) -> list[ActivityVersion]:
        statement = (
            select(ActivityVersion)
            .where(
                ActivityVersion.activity_id == activity_id,
                ActivityVersion.owner_user_id == owner_user_id,
            )
            .order_by(ActivityVersion.version_no.desc())
        )
        return list(self.session.scalars(statement))

    def list_activities(
        self,
        *,
        owner_user_id: UUID,
        registration_status: str | None,
        activity_types: tuple[str, ...],
        usage_enabled: bool | None,
        query: str | None,
        sort: str,
        offset: int,
        limit: int,
    ) -> list[tuple[Activity, ActivityVersion, int]]:
        episode_count = (
            select(func.count(Episode.id))
            .where(
                Episode.activity_id == Activity.id,
                Episode.owner_user_id == owner_user_id,
            )
            .correlate(Activity)
            .scalar_subquery()
        )
        statement = (
            select(Activity, ActivityVersion, episode_count.label("episode_count"))
            .join(
                ActivityVersion,
                (Activity.current_version_id == ActivityVersion.id)
                & (Activity.owner_user_id == ActivityVersion.owner_user_id),
            )
            .where(Activity.owner_user_id == owner_user_id)
        )
        if registration_status is not None:
            statement = statement.where(Activity.registration_status == registration_status)
        if activity_types:
            statement = statement.where(ActivityVersion.activity_type.in_(activity_types))
        if usage_enabled is not None:
            statement = statement.where(Activity.usage_enabled == usage_enabled)
        if query:
            pattern = f"%{query}%"
            statement = statement.where(
                ActivityVersion.title.ilike(pattern)
                | ActivityVersion.organization_text.ilike(pattern)
            )
        if sort == "start_date_desc":
            statement = statement.order_by(
                ActivityVersion.start_date.desc().nulls_last(), Activity.id.desc()
            )
        elif sort == "created_at_desc":
            statement = statement.order_by(Activity.created_at.desc(), Activity.id.desc())
        else:
            statement = statement.order_by(Activity.updated_at.desc(), Activity.id.desc())
        statement = statement.offset(offset).limit(limit)
        return [
            (activity, version, count)
            for activity, version, count in self.session.execute(statement)
        ]

    def list_episodes(
        self, *, activity_id: UUID, owner_user_id: UUID
    ) -> list[tuple[Episode, EpisodeVersion]]:
        statement = (
            select(Episode, EpisodeVersion)
            .join(
                EpisodeVersion,
                (Episode.current_version_id == EpisodeVersion.id)
                & (Episode.owner_user_id == EpisodeVersion.owner_user_id),
            )
            .where(
                Episode.activity_id == activity_id,
                Episode.owner_user_id == owner_user_id,
            )
            .order_by(Episode.updated_at.desc(), Episode.id.desc())
        )
        return list(self.session.execute(statement))

    def get_episode_for_update(self, *, episode_id: UUID, owner_user_id: UUID) -> Episode | None:
        statement = (
            select(Episode)
            .where(Episode.id == episode_id, Episode.owner_user_id == owner_user_id)
            .with_for_update()
        )
        return self.session.scalar(statement)

    def get_episode(self, *, episode_id: UUID, owner_user_id: UUID) -> Episode | None:
        statement = select(Episode).where(
            Episode.id == episode_id,
            Episode.owner_user_id == owner_user_id,
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

    def get_episode_version_by_number(
        self, *, episode_id: UUID, version_no: int, owner_user_id: UUID
    ) -> EpisodeVersion | None:
        statement = select(EpisodeVersion).where(
            EpisodeVersion.episode_id == episode_id,
            EpisodeVersion.version_no == version_no,
            EpisodeVersion.owner_user_id == owner_user_id,
        )
        return self.session.scalar(statement)

    def list_episode_versions(
        self, *, episode_id: UUID, owner_user_id: UUID
    ) -> list[EpisodeVersion]:
        statement = (
            select(EpisodeVersion)
            .where(
                EpisodeVersion.episode_id == episode_id,
                EpisodeVersion.owner_user_id == owner_user_id,
            )
            .order_by(EpisodeVersion.version_no.desc())
        )
        return list(self.session.scalars(statement))

    def list_episode_skills(
        self, *, episode_version_id: UUID, owner_user_id: UUID
    ) -> list[EpisodeVersionSkill]:
        statement = (
            select(EpisodeVersionSkill)
            .where(
                EpisodeVersionSkill.episode_version_id == episode_version_id,
                EpisodeVersionSkill.owner_user_id == owner_user_id,
            )
            .order_by(EpisodeVersionSkill.raw_name)
        )
        return list(self.session.scalars(statement))
