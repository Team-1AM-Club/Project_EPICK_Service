from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Final
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.experience import (
    Activity,
    ActivityVersion,
    Episode,
    EpisodeVersion,
    EpisodeVersionSkill,
    FieldAvailability,
)
from app.repo.experience import ExperienceRepository

_UNSET: Final = object()
_ACTIVITY_COPY_FIELDS: Final = (
    "title",
    "organization_text",
    "organization_availability",
    "activity_type",
    "activity_type_availability",
    "start_date",
    "end_date",
    "period_precision",
    "period_availability",
    "role_text",
    "role_availability",
    "outcome_status",
    "outcome_text",
    "outcome_availability",
    "original_narrative",
)
_EPISODE_COPY_FIELDS: Final = (
    "title",
    "situation_text",
    "situation_availability",
    "problem_text",
    "problem_availability",
    "goal_text",
    "goal_availability",
    "actions_text",
    "actions_availability",
    "decisions_text",
    "decisions_availability",
    "decision_reasons_text",
    "decision_reasons_availability",
    "result_text",
    "result_availability",
    "learning_text",
    "learning_availability",
    "original_narrative",
)


class ExperienceError(Exception):
    pass


class ExperienceNotFoundError(ExperienceError):
    pass


class VersionConflictError(ExperienceError):
    pass


class AvailabilityValidationError(ExperienceError):
    pass


class CompletionRequirementsError(ExperienceError):
    def __init__(self, missing_fields: tuple[str, ...]) -> None:
        super().__init__("completion requirements are not met")
        self.missing_fields = missing_fields


class InvalidExperienceStateError(ExperienceError):
    pass


class ExperienceService:
    """Append-only Experience mutation orchestration.

    This service deliberately does not commit the caller's transaction. A future FastAPI request
    dependency owns the transaction and sets the RLS owner context before invoking it.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ExperienceRepository(session)

    def create_activity(
        self,
        *,
        owner_user_id: UUID,
        title: str,
        organization_text: str | None = None,
        organization_availability: FieldAvailability | str | object = _UNSET,
        activity_type: str | None = None,
        activity_type_availability: FieldAvailability | str | object = _UNSET,
        start_date: date | None = None,
        end_date: date | None = None,
        period_precision: str | None = None,
        period_availability: FieldAvailability | str | object = _UNSET,
        role_text: str | None = None,
        role_availability: FieldAvailability | str | object = _UNSET,
        outcome_status: str | None = None,
        outcome_text: str | None = None,
        outcome_availability: FieldAvailability | str | object = _UNSET,
        original_narrative: str | None = None,
        usage_enabled: bool = True,
    ) -> Activity:
        values = self._activity_values(
            title=title,
            organization_text=organization_text,
            organization_availability=organization_availability,
            activity_type=activity_type,
            activity_type_availability=activity_type_availability,
            start_date=start_date,
            end_date=end_date,
            period_precision=period_precision,
            period_availability=period_availability,
            role_text=role_text,
            role_availability=role_availability,
            outcome_status=outcome_status,
            outcome_text=outcome_text,
            outcome_availability=outcome_availability,
            original_narrative=original_narrative,
        )
        activity = Activity(owner_user_id=owner_user_id, usage_enabled=usage_enabled)
        self.repository.add_activity(activity)
        self.session.flush()
        version = ActivityVersion(
            activity_id=activity.id,
            owner_user_id=owner_user_id,
            version_no=1,
            change_reason=None,
            created_by="USER",
            **values,
        )
        self.repository.add_activity_version(version)
        self.session.flush()
        activity.current_version_id = version.id
        activity.updated_at = func.now()
        self.session.flush()
        return activity

    def append_activity_version(
        self,
        *,
        activity_id: UUID,
        owner_user_id: UUID,
        expected_lock_version: int,
        title: str | object = _UNSET,
        organization_text: str | None | object = _UNSET,
        organization_availability: FieldAvailability | str | object = _UNSET,
        activity_type: str | None | object = _UNSET,
        activity_type_availability: FieldAvailability | str | object = _UNSET,
        start_date: date | None | object = _UNSET,
        end_date: date | None | object = _UNSET,
        period_precision: str | None | object = _UNSET,
        period_availability: FieldAvailability | str | object = _UNSET,
        role_text: str | None | object = _UNSET,
        role_availability: FieldAvailability | str | object = _UNSET,
        outcome_status: str | None | object = _UNSET,
        outcome_text: str | None | object = _UNSET,
        outcome_availability: FieldAvailability | str | object = _UNSET,
        original_narrative: str | None | object = _UNSET,
        usage_enabled: bool | object = _UNSET,
        change_reason: str | None = None,
    ) -> ActivityVersion:
        activity = self.repository.get_activity_for_update(
            activity_id=activity_id, owner_user_id=owner_user_id
        )
        if activity is None or activity.current_version_id is None:
            raise ExperienceNotFoundError("activity does not exist for this owner")
        if activity.lock_version != expected_lock_version:
            raise VersionConflictError("activity has a newer immutable version")
        current = self.repository.get_activity_version(
            activity_version_id=activity.current_version_id, owner_user_id=owner_user_id
        )
        if current is None:
            raise ExperienceNotFoundError("activity current version does not exist for this owner")

        values = {field: getattr(current, field) for field in _ACTIVITY_COPY_FIELDS}
        values.update(
            self._provided_updates(
                title=title,
                organization_text=organization_text,
                organization_availability=organization_availability,
                activity_type=activity_type,
                activity_type_availability=activity_type_availability,
                start_date=start_date,
                end_date=end_date,
                period_precision=period_precision,
                period_availability=period_availability,
                role_text=role_text,
                role_availability=role_availability,
                outcome_status=outcome_status,
                outcome_text=outcome_text,
                outcome_availability=outcome_availability,
                original_narrative=original_narrative,
            )
        )
        self._validate_activity_values(values)
        version = ActivityVersion(
            activity_id=activity.id,
            owner_user_id=owner_user_id,
            version_no=current.version_no + 1,
            change_reason=change_reason,
            created_by="USER",
            **values,
        )
        self.repository.add_activity_version(version)
        self.session.flush()
        activity.current_version_id = version.id
        activity.lock_version += 1
        if usage_enabled is not _UNSET:
            activity.usage_enabled = bool(usage_enabled)
        activity.updated_at = func.now()
        self.session.flush()
        return version

    def create_episode(
        self,
        *,
        owner_user_id: UUID,
        activity_id: UUID,
        title: str,
        situation_text: str | None = None,
        situation_availability: FieldAvailability | str | object = _UNSET,
        problem_text: str | None = None,
        problem_availability: FieldAvailability | str | object = _UNSET,
        goal_text: str | None = None,
        goal_availability: FieldAvailability | str | object = _UNSET,
        actions_text: str | None = None,
        actions_availability: FieldAvailability | str | object = _UNSET,
        decisions_text: str | None = None,
        decisions_availability: FieldAvailability | str | object = _UNSET,
        decision_reasons_text: str | None = None,
        decision_reasons_availability: FieldAvailability | str | object = _UNSET,
        result_text: str | None = None,
        result_availability: FieldAvailability | str | object = _UNSET,
        learning_text: str | None = None,
        learning_availability: FieldAvailability | str | object = _UNSET,
        original_narrative: str | None = None,
        technologies: Sequence[str] = (),
        usage_enabled: bool = True,
    ) -> Episode:
        activity = self.repository.get_activity_for_update(
            activity_id=activity_id, owner_user_id=owner_user_id
        )
        if activity is None or activity.current_version_id is None:
            raise ExperienceNotFoundError("activity does not exist for this owner")
        values = self._episode_values(
            title=title,
            situation_text=situation_text,
            situation_availability=situation_availability,
            problem_text=problem_text,
            problem_availability=problem_availability,
            goal_text=goal_text,
            goal_availability=goal_availability,
            actions_text=actions_text,
            actions_availability=actions_availability,
            decisions_text=decisions_text,
            decisions_availability=decisions_availability,
            decision_reasons_text=decision_reasons_text,
            decision_reasons_availability=decision_reasons_availability,
            result_text=result_text,
            result_availability=result_availability,
            learning_text=learning_text,
            learning_availability=learning_availability,
            original_narrative=original_narrative,
        )
        episode = Episode(
            owner_user_id=owner_user_id,
            activity_id=activity.id,
            usage_enabled=usage_enabled,
        )
        self.repository.add_episode(episode)
        self.session.flush()
        version = EpisodeVersion(
            episode_id=episode.id,
            owner_user_id=owner_user_id,
            version_no=1,
            activity_id=activity.id,
            activity_version_id=activity.current_version_id,
            change_reason=None,
            **values,
        )
        self.repository.add_episode_version(version)
        self.session.flush()
        self._persist_episode_skills(
            episode_version_id=version.id,
            owner_user_id=owner_user_id,
            technologies=technologies,
        )
        episode.current_version_id = version.id
        episode.updated_at = func.now()
        self.session.flush()
        return episode

    def append_episode_version(
        self,
        *,
        episode_id: UUID,
        owner_user_id: UUID,
        expected_lock_version: int,
        title: str | object = _UNSET,
        situation_text: str | None | object = _UNSET,
        situation_availability: FieldAvailability | str | object = _UNSET,
        problem_text: str | None | object = _UNSET,
        problem_availability: FieldAvailability | str | object = _UNSET,
        goal_text: str | None | object = _UNSET,
        goal_availability: FieldAvailability | str | object = _UNSET,
        actions_text: str | None | object = _UNSET,
        actions_availability: FieldAvailability | str | object = _UNSET,
        decisions_text: str | None | object = _UNSET,
        decisions_availability: FieldAvailability | str | object = _UNSET,
        decision_reasons_text: str | None | object = _UNSET,
        decision_reasons_availability: FieldAvailability | str | object = _UNSET,
        result_text: str | None | object = _UNSET,
        result_availability: FieldAvailability | str | object = _UNSET,
        learning_text: str | None | object = _UNSET,
        learning_availability: FieldAvailability | str | object = _UNSET,
        original_narrative: str | None | object = _UNSET,
        technologies: Sequence[str] | object = _UNSET,
        usage_enabled: bool | object = _UNSET,
        change_reason: str | None = None,
    ) -> EpisodeVersion:
        episode = self.repository.get_episode_for_update(
            episode_id=episode_id, owner_user_id=owner_user_id
        )
        if episode is None or episode.current_version_id is None:
            raise ExperienceNotFoundError("episode does not exist for this owner")
        if episode.lock_version != expected_lock_version:
            raise VersionConflictError("episode has a newer immutable version")
        current = self.repository.get_episode_version(
            episode_version_id=episode.current_version_id, owner_user_id=owner_user_id
        )
        activity = self.repository.get_activity_for_update(
            activity_id=episode.activity_id, owner_user_id=owner_user_id
        )
        if current is None or activity is None or activity.current_version_id is None:
            raise ExperienceNotFoundError("episode source records do not exist for this owner")

        values = {field: getattr(current, field) for field in _EPISODE_COPY_FIELDS}
        values.update(
            self._provided_updates(
                title=title,
                situation_text=situation_text,
                situation_availability=situation_availability,
                problem_text=problem_text,
                problem_availability=problem_availability,
                goal_text=goal_text,
                goal_availability=goal_availability,
                actions_text=actions_text,
                actions_availability=actions_availability,
                decisions_text=decisions_text,
                decisions_availability=decisions_availability,
                decision_reasons_text=decision_reasons_text,
                decision_reasons_availability=decision_reasons_availability,
                result_text=result_text,
                result_availability=result_availability,
                learning_text=learning_text,
                learning_availability=learning_availability,
                original_narrative=original_narrative,
            )
        )
        self._validate_episode_values(values)
        version = EpisodeVersion(
            episode_id=episode.id,
            owner_user_id=owner_user_id,
            version_no=current.version_no + 1,
            activity_id=episode.activity_id,
            activity_version_id=activity.current_version_id,
            change_reason=change_reason,
            **values,
        )
        self.repository.add_episode_version(version)
        self.session.flush()
        if technologies is _UNSET:
            technologies = tuple(
                skill.raw_name
                for skill in self.repository.list_episode_skills(
                    episode_version_id=current.id,
                    owner_user_id=owner_user_id,
                )
            )
        self._persist_episode_skills(
            episode_version_id=version.id,
            owner_user_id=owner_user_id,
            technologies=technologies,
        )
        episode.current_version_id = version.id
        episode.lock_version += 1
        if usage_enabled is not _UNSET:
            episode.usage_enabled = bool(usage_enabled)
        episode.updated_at = func.now()
        self.session.flush()
        return version

    def complete_activity(
        self, *, activity_id: UUID, owner_user_id: UUID, expected_lock_version: int
    ) -> ActivityVersion:
        activity = self.repository.get_activity_for_update(
            activity_id=activity_id,
            owner_user_id=owner_user_id,
        )
        if activity is None or activity.current_version_id is None:
            raise ExperienceNotFoundError("activity does not exist for this owner")
        if activity.lock_version != expected_lock_version:
            raise VersionConflictError("activity has a newer immutable version")
        if activity.registration_status != "DRAFT" or activity.deletion_status != "ACTIVE":
            raise InvalidExperienceStateError("activity cannot transition to completed")
        current = self.repository.get_activity_version(
            activity_version_id=activity.current_version_id,
            owner_user_id=owner_user_id,
        )
        if current is None:
            raise ExperienceNotFoundError("activity current version does not exist for this owner")
        self._validate_activity_completion(current)
        version = self.append_activity_version(
            activity_id=activity_id,
            owner_user_id=owner_user_id,
            expected_lock_version=expected_lock_version,
            change_reason="COMPLETED",
        )
        activity.registration_status = "COMPLETED"
        self.session.flush()
        return version

    def complete_episode(
        self, *, episode_id: UUID, owner_user_id: UUID, expected_lock_version: int
    ) -> EpisodeVersion:
        episode = self.repository.get_episode_for_update(
            episode_id=episode_id,
            owner_user_id=owner_user_id,
        )
        if episode is None or episode.current_version_id is None:
            raise ExperienceNotFoundError("episode does not exist for this owner")
        if episode.lock_version != expected_lock_version:
            raise VersionConflictError("episode has a newer immutable version")
        if episode.registration_status != "DRAFT" or episode.deletion_status != "ACTIVE":
            raise InvalidExperienceStateError("episode cannot transition to completed")
        version = self.append_episode_version(
            episode_id=episode_id,
            owner_user_id=owner_user_id,
            expected_lock_version=expected_lock_version,
            change_reason="COMPLETED",
        )
        episode.registration_status = "COMPLETED"
        self.session.flush()
        return version

    @staticmethod
    def _provided_updates(**values: object) -> dict[str, object]:
        return {field: value for field, value in values.items() if value is not _UNSET}

    def _persist_episode_skills(
        self,
        *,
        episode_version_id: UUID,
        owner_user_id: UUID,
        technologies: Sequence[str] | object,
    ) -> None:
        if not isinstance(technologies, Sequence) or isinstance(technologies, str):
            raise AvailabilityValidationError("technologies must be a sequence of strings")
        normalized_names: list[str] = []
        seen_names: set[str] = set()
        for technology in technologies:
            if not isinstance(technology, str) or not technology.strip():
                raise AvailabilityValidationError("technology must be a non-empty string")
            normalized = technology.strip()
            normalized_key = normalized.casefold()
            if normalized_key not in seen_names:
                seen_names.add(normalized_key)
                normalized_names.append(normalized)
        for technology in normalized_names:
            self.repository.add_episode_version_skill(
                EpisodeVersionSkill(
                    episode_version_id=episode_version_id,
                    owner_user_id=owner_user_id,
                    raw_name=technology,
                    origin="USER_INPUT",
                )
            )
        self.session.flush()

    def _activity_values(
        self,
        *,
        title: str,
        organization_text: str | None,
        organization_availability: FieldAvailability | str | object,
        activity_type: str | None,
        activity_type_availability: FieldAvailability | str | object,
        start_date: date | None,
        end_date: date | None,
        period_precision: str | None,
        period_availability: FieldAvailability | str | object,
        role_text: str | None,
        role_availability: FieldAvailability | str | object,
        outcome_status: str | None,
        outcome_text: str | None,
        outcome_availability: FieldAvailability | str | object,
        original_narrative: str | None,
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "title": title,
            "organization_text": organization_text,
            "organization_availability": self._resolve_availability(
                organization_availability, organization_text is not None
            ),
            "activity_type": activity_type,
            "activity_type_availability": self._resolve_availability(
                activity_type_availability, activity_type is not None
            ),
            "start_date": start_date,
            "end_date": end_date,
            "period_precision": period_precision,
            "period_availability": self._resolve_availability(
                period_availability,
                any(value is not None for value in (start_date, end_date, period_precision)),
            ),
            "role_text": role_text,
            "role_availability": self._resolve_availability(
                role_availability, role_text is not None
            ),
            "outcome_status": outcome_status,
            "outcome_text": outcome_text,
            "outcome_availability": self._resolve_availability(
                outcome_availability, outcome_status is not None or outcome_text is not None
            ),
            "original_narrative": original_narrative,
        }
        self._validate_activity_values(values)
        return values

    def _episode_values(
        self,
        *,
        title: str,
        situation_text: str | None,
        situation_availability: FieldAvailability | str | object,
        problem_text: str | None,
        problem_availability: FieldAvailability | str | object,
        goal_text: str | None,
        goal_availability: FieldAvailability | str | object,
        actions_text: str | None,
        actions_availability: FieldAvailability | str | object,
        decisions_text: str | None,
        decisions_availability: FieldAvailability | str | object,
        decision_reasons_text: str | None,
        decision_reasons_availability: FieldAvailability | str | object,
        result_text: str | None,
        result_availability: FieldAvailability | str | object,
        learning_text: str | None,
        learning_availability: FieldAvailability | str | object,
        original_narrative: str | None,
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "title": title,
            "original_narrative": original_narrative,
        }
        for field_name, text_value, availability in (
            ("situation", situation_text, situation_availability),
            ("problem", problem_text, problem_availability),
            ("goal", goal_text, goal_availability),
            ("actions", actions_text, actions_availability),
            ("decisions", decisions_text, decisions_availability),
            ("decision_reasons", decision_reasons_text, decision_reasons_availability),
            ("result", result_text, result_availability),
            ("learning", learning_text, learning_availability),
        ):
            values[f"{field_name}_text"] = text_value
            values[f"{field_name}_availability"] = self._resolve_availability(
                availability,
                text_value is not None,
            )
        self._validate_episode_values(values)
        return values

    @staticmethod
    def _resolve_availability(value: FieldAvailability | str | object, has_value: bool) -> str:
        if value is _UNSET:
            return (
                FieldAvailability.PROVIDED.value
                if has_value
                else FieldAvailability.NOT_PROVIDED.value
            )
        return ExperienceService._normalize_availability(value)

    @staticmethod
    def _normalize_availability(value: FieldAvailability | str | object) -> str:
        normalized = value.value if isinstance(value, FieldAvailability) else value
        if (
            not isinstance(normalized, str)
            or normalized not in FieldAvailability._value2member_map_
        ):
            raise AvailabilityValidationError("unknown field availability")
        return normalized

    def _validate_activity_values(self, values: dict[str, object]) -> None:
        self._validate_title(values)
        self._validate_pair(values, "organization")
        self._validate_single_value(values, "activity_type")
        self._validate_pair(values, "role")
        self._validate_multi_value(
            values,
            "period_availability",
            "start_date",
            "end_date",
            "period_precision",
        )
        self._validate_multi_value(values, "outcome_availability", "outcome_status", "outcome_text")

    def _validate_episode_values(self, values: dict[str, object]) -> None:
        self._validate_title(values)
        for name in (
            "situation",
            "problem",
            "goal",
            "actions",
            "decisions",
            "decision_reasons",
            "result",
            "learning",
        ):
            self._validate_pair(values, name)

    def _validate_activity_completion(self, version: ActivityVersion) -> None:
        missing: list[str] = []
        if not version.title:
            missing.append("title")
        if version.organization_availability != FieldAvailability.PROVIDED.value:
            missing.append("organization")
        if version.activity_type_availability != FieldAvailability.PROVIDED.value:
            missing.append("activity_type")
        if version.period_availability != FieldAvailability.PROVIDED.value:
            missing.append("period")
        if version.role_availability != FieldAvailability.PROVIDED.value:
            missing.append("role")
        if version.outcome_status is None:
            missing.append("outcome.status")
        if missing:
            raise CompletionRequirementsError(tuple(missing))

    @staticmethod
    def _validate_title(values: dict[str, object]) -> None:
        if not isinstance(values.get("title"), str) or not values["title"]:
            raise AvailabilityValidationError("title must be present")

    def _validate_pair(self, values: dict[str, object], field_name: str) -> None:
        availability_name = f"{field_name}_availability"
        value_name = f"{field_name}_text"
        availability = self._normalize_availability(values[availability_name])
        values[availability_name] = availability
        is_provided = values.get(value_name) is not None
        if (availability == FieldAvailability.PROVIDED.value) != is_provided:
            raise AvailabilityValidationError(f"{field_name} value must match its availability")

    def _validate_single_value(self, values: dict[str, object], field_name: str) -> None:
        availability_name = f"{field_name}_availability"
        availability = self._normalize_availability(values[availability_name])
        values[availability_name] = availability
        is_provided = values.get(field_name) is not None
        if (availability == FieldAvailability.PROVIDED.value) != is_provided:
            raise AvailabilityValidationError(f"{field_name} value must match its availability")

    def _validate_multi_value(
        self, values: dict[str, object], availability_name: str, *value_names: str
    ) -> None:
        availability = self._normalize_availability(values[availability_name])
        values[availability_name] = availability
        has_value = any(values.get(value_name) is not None for value_name in value_names)
        if (availability == FieldAvailability.PROVIDED.value) != has_value:
            raise AvailabilityValidationError(
                f"{availability_name.removesuffix('_availability')} "
                "value must match its availability"
            )
