from __future__ import annotations

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.experience import Episode, EpisodeVersion
from app.models.identity import User
from app.models.jobs import Job
from app.models.lifecycle_operations import Notification
from app.services.experience import ExperienceService
from app.services.jobs import JobService
from app.services.lifecycle_operations import (
    LifecycleOperationNotFoundError,
    LifecycleOperationsService,
    LifecycleOperationTransitionError,
    LifecycleOperationValidationError,
)


@pytest.fixture(autouse=True)
def clean_lifecycle_operation_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


def _create_owner(session: Session, display_name: str) -> User:
    owner = User(display_name=display_name, locale="ko-KR", timezone="Asia/Seoul")
    session.add(owner)
    session.flush()
    return owner


def _create_two_episodes(session: Session, *, owner: User) -> tuple[Episode, Episode]:
    experience = ExperienceService(session)
    activity = experience.create_activity(owner_user_id=owner.id, title="Lifecycle activity")
    first = experience.create_episode(
        owner_user_id=owner.id,
        activity_id=activity.id,
        title="First Episode",
        situation_text="The first immutable record.",
    )
    second = experience.create_episode(
        owner_user_id=owner.id,
        activity_id=activity.id,
        title="Second Episode",
        situation_text="The target immutable record.",
    )
    assert first.current_version_id is not None
    assert second.current_version_id is not None
    return first, second


def _paused_job(session: Session, *, owner: User) -> Job:
    jobs = JobService(session)
    accepted = jobs.accept_job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        idempotency_key="checkpoint-job",
        request_hash="a" * 64,
        analysis_input_version="analysis-v1",
    )
    jobs.mark_dispatch_enqueued(owner_user_id=owner.id, job_id=accepted.job.id)
    lease = jobs.claim_execution(owner_user_id=owner.id, job_id=accepted.job.id)
    assert lease is not None
    assert jobs.pause_execution(
        owner_user_id=owner.id,
        job_id=accepted.job.id,
        lease_id=lease.id,
        execution_fence=lease.execution_fence,
        owner_deletion_epoch=lease.owner_deletion_epoch,
        status="PAUSED_RATE_LIMIT",
        action_code="RETRY",
    )
    assert accepted.job.status == "PAUSED_RATE_LIMIT"
    return accepted.job


def test_inference_decision_is_append_only_and_does_not_overwrite_episode_version(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Inference owner")
    first, _ = _create_two_episodes(db_session, owner=owner)
    assert first.current_version_id is not None
    original_version_id = first.current_version_id
    original = db_session.get(EpisodeVersion, original_version_id)
    assert original is not None

    operations = LifecycleOperationsService(db_session)
    suggestion = operations.create_inference_suggestion(
        owner_user_id=owner.id,
        episode_id=first.id,
        episode_version_id=original_version_id,
        suggestion_type="SKILL_NORMALIZATION",
        proposed_value={"skill": "PostgreSQL"},
        model_policy_version="inference-policy-v1",
    )
    evidence = operations.add_inference_suggestion_source(
        owner_user_id=owner.id,
        suggestion_id=suggestion.id,
        episode_version_id=original_version_id,
        field_name="situation_text",
    )
    decision = operations.record_inference_decision(
        owner_user_id=owner.id,
        suggestion_id=suggestion.id,
        decision="MODIFIED",
        modified_value={"skill": "PostgreSQL 16"},
        reason="Use the explicitly selected version.",
    )
    db_session.commit()

    db_session.refresh(first)
    db_session.refresh(suggestion)
    assert evidence.episode_version_id == original_version_id
    assert decision.decision_no == 1
    assert suggestion.status == "DECIDED"
    assert first.current_version_id == original_version_id
    assert db_session.get(EpisodeVersion, original_version_id).title == original.title
    with pytest.raises(OperationalError, match="append-only"):
        db_session.execute(
            text("UPDATE inference_decisions SET decision = 'REJECTED' WHERE id = :id"),
            {"id": decision.id},
        )
    db_session.rollback()
    with pytest.raises(LifecycleOperationValidationError, match="sensitive"):
        operations.create_inference_suggestion(
            owner_user_id=owner.id,
            episode_id=first.id,
            episode_version_id=original_version_id,
            suggestion_type="UNSAFE",
            proposed_value={"prompt": "raw model prompt"},
            model_policy_version="inference-policy-v1",
        )


def test_duplicate_merge_needs_current_merge_decision_and_keeps_original_episode(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Duplicate owner")
    first, second = _create_two_episodes(db_session, owner=owner)
    assert first.current_version_id is not None
    assert second.current_version_id is not None
    operations = LifecycleOperationsService(db_session)
    suggestion = operations.create_duplicate_suggestion(
        owner_user_id=owner.id,
        first_episode_id=first.id,
        first_episode_version_id=first.current_version_id,
        second_episode_id=second.id,
        second_episode_version_id=second.current_version_id,
        reason="The two entries describe the same deliverable.",
    )
    keep_separate = operations.record_duplicate_decision(
        owner_user_id=owner.id,
        suggestion_id=suggestion.id,
        decision="KEEP_SEPARATE",
    )
    with pytest.raises(LifecycleOperationTransitionError, match="MERGE"):
        operations.create_merge_record(
            owner_user_id=owner.id,
            decision_id=keep_separate.id,
            source_episode_id=suggestion.left_episode_id,
            source_episode_version_id=suggestion.left_episode_version_id,
            target_episode_id=suggestion.right_episode_id,
            target_episode_version_id=suggestion.right_episode_version_id,
        )

    merge_decision = operations.record_duplicate_decision(
        owner_user_id=owner.id,
        suggestion_id=suggestion.id,
        decision="MERGE",
    )
    merge = operations.create_merge_record(
        owner_user_id=owner.id,
        decision_id=merge_decision.id,
        source_episode_id=suggestion.left_episode_id,
        source_episode_version_id=suggestion.left_episode_version_id,
        target_episode_id=suggestion.right_episode_id,
        target_episode_version_id=suggestion.right_episode_version_id,
    )
    db_session.commit()

    source = db_session.get(Episode, suggestion.left_episode_id)
    target = db_session.get(Episode, suggestion.right_episode_id)
    assert source is not None and target is not None
    assert source.current_version_id == suggestion.left_episode_version_id
    assert target.current_version_id == merge.result_episode_version_id
    assert db_session.get(EpisodeVersion, merge.source_episode_version_id) is not None
    assert db_session.get(EpisodeVersion, merge.target_episode_version_id) is not None


def test_checkpoint_recovery_validates_current_execution_and_replays_idempotently(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Checkpoint owner")
    job = _paused_job(db_session, owner=owner)
    operations = LifecycleOperationsService(db_session)
    with pytest.raises(LifecycleOperationValidationError, match="sensitive"):
        operations.create_job_checkpoint(
            owner_user_id=owner.id,
            job_id=job.id,
            execution_fence=job.execution_fence,
            owner_deletion_epoch=job.owner_deletion_epoch,
            analysis_input_version=job.analysis_input_version,
            resume_stage="fetch",
            resume_payload={"prompt": "must never be persisted"},
        )
    with pytest.raises(LifecycleOperationTransitionError, match="current fence"):
        operations.create_job_checkpoint(
            owner_user_id=owner.id,
            job_id=job.id,
            execution_fence=job.execution_fence + 1,
            owner_deletion_epoch=job.owner_deletion_epoch,
            analysis_input_version=job.analysis_input_version,
            resume_stage="fetch",
        )
    checkpoint = operations.create_job_checkpoint(
        owner_user_id=owner.id,
        job_id=job.id,
        execution_fence=job.execution_fence,
        owner_deletion_epoch=job.owner_deletion_epoch,
        analysis_input_version=job.analysis_input_version,
        resume_stage="fetch",
        state_ref="fetch:checkpoint-0001",
        resume_payload={"cursor": "opaque-page-token"},
    )
    db_session.commit()
    with pytest.raises(OperationalError, match="append-only"):
        db_session.execute(
            text("UPDATE job_checkpoints SET resume_stage = 'parse' WHERE id = :id"),
            {"id": checkpoint.id},
        )
    db_session.rollback()
    resume = operations.resume_rate_limited_job_from_checkpoint(
        owner_user_id=owner.id,
        job_id=job.id,
        checkpoint_id=checkpoint.id,
        from_stage="fetch",
        idempotency_key="resume-key",
        request_hash="b" * 64,
    )
    db_session.commit()

    assert resume.replayed is False
    assert resume.job.execution_fence == checkpoint.execution_fence + 1
    assert resume.command is not None
    assert resume.command.payload["checkpoint_id"] == str(checkpoint.id)
    assert resume.outbox_message is not None
    assert resume.outbox_message.payload["checkpoint_id"] == str(checkpoint.id)
    replay = operations.resume_rate_limited_job_from_checkpoint(
        owner_user_id=owner.id,
        job_id=job.id,
        checkpoint_id=checkpoint.id,
        from_stage="fetch",
        idempotency_key="resume-key",
        request_hash="b" * 64,
    )
    assert replay.replayed is True
    assert replay.command is not None and replay.command.id == resume.command.id


def test_notifications_are_owner_scoped_and_accept_only_safe_navigation(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Notification owner")
    other = _create_owner(db_session, "Other notification owner")
    operations = LifecycleOperationsService(db_session)
    notification = operations.create_notification(
        owner_user_id=owner.id,
        notification_type="JOB_REQUIRES_ACTION",
        severity="WARNING",
        title="Job requires your action",
        safe_message="Please choose whether to retry the paused task.",
        action_url="/jobs/example",
    )
    with pytest.raises(LifecycleOperationValidationError, match="safe relative"):
        operations.create_notification(
            owner_user_id=owner.id,
            notification_type="UNSAFE_LINK",
            severity="INFO",
            title="Unsafe",
            safe_message="Unsafe",
            action_url="https://external.example.test/redirect",
        )
    with pytest.raises(LifecycleOperationNotFoundError):
        operations.mark_notification_read(owner_user_id=other.id, notification_id=notification.id)
    read = operations.mark_notification_read(
        owner_user_id=owner.id, notification_id=notification.id
    )
    archived = operations.archive_notification(
        owner_user_id=owner.id, notification_id=notification.id
    )
    db_session.commit()

    stored = db_session.scalar(select(Notification).where(Notification.id == notification.id))
    assert stored is not None
    assert read.read_at is not None
    assert archived.archived_at is not None
