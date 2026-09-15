from __future__ import annotations

from threading import Event, Thread

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.experience import (
    Activity,
    ActivityVersion,
    Episode,
    EpisodeVersion,
    FieldAvailability,
)
from app.models.identity import User
from app.services.experience import (
    AvailabilityValidationError,
    ExperienceService,
    VersionConflictError,
)


@pytest.fixture(autouse=True)
def clean_experience_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE experience_field_provenance, episode_version_skills, "
                "episode_versions, episodes, activity_versions, activities CASCADE"
            )
        )
    yield


def _create_owner(db_session: Session, display_name: str) -> User:
    user = User(display_name=display_name, locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(user)
    db_session.flush()
    return user


def _activity_version(*, activity: Activity, owner_user_id, version_no: int) -> ActivityVersion:
    return ActivityVersion(
        activity_id=activity.id,
        owner_user_id=owner_user_id,
        version_no=version_no,
        title=f"Activity {version_no}",
        organization_availability=FieldAvailability.NOT_PROVIDED,
        activity_type_availability=FieldAvailability.NOT_PROVIDED,
        period_availability=FieldAvailability.NOT_PROVIDED,
        role_availability=FieldAvailability.NOT_PROVIDED,
        outcome_availability=FieldAvailability.NOT_PROVIDED,
        created_by="USER",
    )


def test_activity_type_single_value_rejects_mismatched_availability(db_session: Session) -> None:
    service = ExperienceService(db_session)
    owner_user_id = _create_owner(db_session, "Activity type availability owner").id

    with pytest.raises(AvailabilityValidationError):
        service.create_activity(
            owner_user_id=owner_user_id,
            title="Invalid activity type availability",
            activity_type_availability=FieldAvailability.PROVIDED,
        )

    with pytest.raises(AvailabilityValidationError):
        service.create_activity(
            owner_user_id=owner_user_id,
            title="Invalid activity type value",
            activity_type="PROJECT",
            activity_type_availability=FieldAvailability.SKIPPED,
        )


def test_activity_update_appends_an_immutable_version_and_advances_own_pointer(
    db_session: Session,
) -> None:
    service = ExperienceService(db_session)
    owner_user_id = _create_owner(db_session, "Version owner").id
    activity = service.create_activity(
        owner_user_id=owner_user_id,
        title="Original title",
        activity_type="PROJECT",
        organization_text="Synthetic organization",
        role_text="Backend developer",
    )
    db_session.commit()
    original_version_id = activity.current_version_id

    updated_version = service.append_activity_version(
        activity_id=activity.id,
        owner_user_id=owner_user_id,
        expected_lock_version=activity.lock_version,
        title="Updated title",
    )
    db_session.commit()

    versions = (
        db_session.query(ActivityVersion)
        .filter(ActivityVersion.activity_id == activity.id)
        .order_by(ActivityVersion.version_no)
        .all()
    )
    db_session.refresh(activity)

    assert [version.version_no for version in versions] == [1, 2]
    assert versions[0].title == "Original title"
    assert updated_version.title == "Updated title"
    assert activity.current_version_id == updated_version.id
    assert activity.current_version_id != original_version_id
    assert "Updated title" not in repr(updated_version)


def test_cross_owner_and_cross_activity_episode_links_fail_at_database_boundary(
    db_session: Session,
) -> None:
    owner_a = _create_owner(db_session, "Owner A").id
    owner_b = _create_owner(db_session, "Owner B").id
    activity_a = Activity(owner_user_id=owner_a)
    activity_b = Activity(owner_user_id=owner_b)
    db_session.add_all([activity_a, activity_b])
    db_session.flush()

    invalid_episode = Episode(owner_user_id=owner_a, activity_id=activity_b.id)
    db_session.add(invalid_episode)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_episode_version_cannot_reference_a_version_from_another_activity(
    db_session: Session,
) -> None:
    owner_user_id = _create_owner(db_session, "Cross activity owner").id
    activity_a = Activity(owner_user_id=owner_user_id)
    activity_b = Activity(owner_user_id=owner_user_id)
    db_session.add_all([activity_a, activity_b])
    db_session.flush()
    activity_version_a = _activity_version(
        activity=activity_a, owner_user_id=owner_user_id, version_no=1
    )
    activity_version_b = _activity_version(
        activity=activity_b, owner_user_id=owner_user_id, version_no=1
    )
    episode = Episode(owner_user_id=owner_user_id, activity_id=activity_a.id)
    db_session.add_all([activity_version_a, activity_version_b, episode])
    db_session.flush()

    invalid_version = EpisodeVersion(
        episode_id=episode.id,
        owner_user_id=owner_user_id,
        version_no=1,
        activity_id=activity_a.id,
        activity_version_id=activity_version_b.id,
        title="Invalid cross activity version",
        situation_availability=FieldAvailability.NOT_PROVIDED,
        problem_availability=FieldAvailability.NOT_PROVIDED,
        goal_availability=FieldAvailability.NOT_PROVIDED,
        actions_availability=FieldAvailability.NOT_PROVIDED,
        decisions_availability=FieldAvailability.NOT_PROVIDED,
        decision_reasons_availability=FieldAvailability.NOT_PROVIDED,
        result_availability=FieldAvailability.NOT_PROVIDED,
        learning_availability=FieldAvailability.NOT_PROVIDED,
    )
    db_session.add(invalid_version)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_value_and_availability_pair_rejects_provided_without_value(db_session: Session) -> None:
    owner_user_id = _create_owner(db_session, "Availability owner").id
    activity = Activity(owner_user_id=owner_user_id)
    db_session.add(activity)
    db_session.flush()
    invalid_version = ActivityVersion(
        activity_id=activity.id,
        owner_user_id=owner_user_id,
        version_no=1,
        title="Invalid availability",
        organization_availability=FieldAvailability.PROVIDED,
        activity_type_availability=FieldAvailability.SKIPPED,
        period_availability=FieldAvailability.SKIPPED,
        role_availability=FieldAvailability.SKIPPED,
        outcome_availability=FieldAvailability.SKIPPED,
        created_by="USER",
    )
    db_session.add(invalid_version)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_stale_activity_lock_version_cannot_append_a_duplicate_version(db_session: Session) -> None:
    service = ExperienceService(db_session)
    owner_user_id = _create_owner(db_session, "Concurrent owner").id
    activity = service.create_activity(
        owner_user_id=owner_user_id,
        title="Concurrent update",
        activity_type="PROJECT",
        organization_text="Synthetic organization",
        role_text="Backend developer",
    )
    db_session.commit()
    original_lock_version = activity.lock_version

    service.append_activity_version(
        activity_id=activity.id,
        owner_user_id=owner_user_id,
        expected_lock_version=original_lock_version,
        title="First update",
    )
    db_session.commit()

    with pytest.raises(VersionConflictError):
        service.append_activity_version(
            activity_id=activity.id,
            owner_user_id=owner_user_id,
            expected_lock_version=original_lock_version,
            title="Stale update",
        )


def test_concurrent_activity_writers_serialize_and_do_not_duplicate_version_numbers(
    db_session: Session, migrated_engine: Engine
) -> None:
    service = ExperienceService(db_session)
    owner_user_id = _create_owner(db_session, "Parallel writer").id
    activity = service.create_activity(
        owner_user_id=owner_user_id,
        title="Concurrent update",
        activity_type="PROJECT",
        organization_text="Synthetic organization",
        role_text="Backend developer",
    )
    db_session.commit()

    first_session = sessionmaker(bind=migrated_engine, expire_on_commit=False)()
    second_session = sessionmaker(bind=migrated_engine, expire_on_commit=False)()
    second_started = Event()
    outcome: list[Exception | None] = []

    def second_writer() -> None:
        try:
            second_started.set()
            ExperienceService(second_session).append_activity_version(
                activity_id=activity.id,
                owner_user_id=owner_user_id,
                expected_lock_version=1,
                title="Second writer",
            )
            second_session.commit()
            outcome.append(None)
        except Exception as error:  # pragma: no cover - asserted by the calling thread
            second_session.rollback()
            outcome.append(error)
        finally:
            second_session.close()

    try:
        ExperienceService(first_session).append_activity_version(
            activity_id=activity.id,
            owner_user_id=owner_user_id,
            expected_lock_version=1,
            title="First writer",
        )
        writer = Thread(target=second_writer)
        writer.start()
        assert second_started.wait(timeout=1)
        first_session.commit()
        writer.join(timeout=5)
        assert not writer.is_alive()
    finally:
        first_session.close()
        second_session.close()

    assert len(outcome) == 1
    assert isinstance(outcome[0], VersionConflictError)
    versions = (
        db_session.query(ActivityVersion)
        .filter(ActivityVersion.activity_id == activity.id)
        .order_by(ActivityVersion.version_no)
        .all()
    )
    assert [version.version_no for version in versions] == [1, 2]
