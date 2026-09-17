from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.application_workspace import Company
from app.models.deletion import DeletionTarget
from app.models.experience import Episode
from app.models.identity import User
from app.models.jobs import OutboxMessage
from app.models.privacy_controls import AnalyticsEvent, SensitivityFinding
from app.models.sources import Source
from app.models.w2_commit_operations import W2CommitOperation
from app.services.deletion import (
    DeletionOrchestrationService,
    StaleDeletionAcknowledgementError,
)
from app.services.experience import ExperienceService
from app.services.jobs import JobService
from app.services.privacy_controls import PrivacyControlsService
from app.services.w2_commit_gate import W2CommitGateService


@pytest.fixture(autouse=True)
def clean_privacy_and_deletion_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


def _create_owner(session: Session, display_name: str) -> User:
    owner = User(display_name=display_name, locale="ko-KR", timezone="Asia/Seoul")
    session.add(owner)
    session.flush()
    return owner


def _create_episode(session: Session, *, owner: User) -> Episode:
    experience = ExperienceService(session)
    activity = experience.create_activity(owner_user_id=owner.id, title="Privacy activity")
    episode = experience.create_episode(
        owner_user_id=owner.id,
        activity_id=activity.id,
        title="Privacy episode",
        situation_text="A locally stored experience narrative.",
    )
    assert episode.current_version_id is not None
    return episode


def test_settings_consent_analytics_and_sensitivity_gate_are_owner_scoped(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Privacy owner")
    episode = _create_episode(db_session, owner=owner)
    assert episode.current_version_id is not None
    controls = PrivacyControlsService(db_session)

    settings = controls.get_or_create_user_settings(owner_user_id=owner.id)
    preferences = controls.get_or_create_recommendation_preferences(owner_user_id=owner.id)
    updated = controls.update_recommendation_preferences(
        owner_user_id=owner.id,
        expected_lock_version=preferences.lock_version,
        default_candidate_limit=7,
    )
    assert settings.locale == "ko-KR"
    assert updated.default_candidate_limit == 7
    assert updated.lock_version == 2

    consent = controls.record_consent(
        owner_user_id=owner.id,
        consent_type="ANALYTICS",
        policy_version="analytics-v1",
        granted=True,
    )
    event = controls.record_analytics_event(
        owner_user_id=owner.id,
        event_key="feedback-saved-1",
        pseudonymous_subject_id="anonymous-subject-1",
        event_type="FEEDBACK_SUBMITTED",
        consent_policy_version=consent.policy_version,
        allowed_properties={"feedback_id": str(uuid4())},
    )
    assert event.owner_user_id == owner.id
    db_session.commit()

    with pytest.raises(OperationalError, match="append-only"):
        db_session.execute(
            text("UPDATE consents SET granted = false WHERE id = :consent_id"),
            {"consent_id": consent.id},
        )
    db_session.rollback()
    assert db_session.get(AnalyticsEvent, event.id) is not None

    clean_assessment = controls.create_sensitivity_assessment(
        owner_user_id=owner.id,
        episode_version_id=episode.current_version_id,
        detector_version="sensitivity-v1",
    )
    controls.complete_sensitivity_assessment_without_findings(
        owner_user_id=owner.id, assessment_id=clean_assessment.id
    )
    clean_eligibility = controls.external_processing_eligibility(
        owner_user_id=owner.id, episode_version_id=episode.current_version_id
    )
    assert clean_eligibility.eligible is True
    assert clean_eligibility.mode == "NO_SENSITIVE_FINDINGS"

    assessment = controls.create_sensitivity_assessment(
        owner_user_id=owner.id,
        episode_version_id=episode.current_version_id,
        detector_version="sensitivity-v2",
    )
    finding = controls.add_sensitivity_finding(
        owner_user_id=owner.id,
        assessment_id=assessment.id,
        category="PERSONAL_DATA",
        field_name="situation_text",
        severity="HIGH",
        source_span_start=0,
        source_span_end=12,
    )
    blocked = controls.external_processing_eligibility(
        owner_user_id=owner.id, episode_version_id=episode.current_version_id
    )
    assert blocked.eligible is False
    assert blocked.reason_code == "SENSITIVITY_REVIEW_REQUIRED"
    decision = controls.record_sensitivity_decision(
        owner_user_id=owner.id,
        assessment_id=assessment.id,
        decision="REDACT_BEFORE_EXTERNAL",
    )
    eligible = controls.external_processing_eligibility(
        owner_user_id=owner.id, episode_version_id=episode.current_version_id
    )
    db_session.commit()

    stored_finding = db_session.get(SensitivityFinding, finding.id)
    assert stored_finding is not None
    assert stored_finding.category == "PERSONAL_DATA"
    assert eligible.eligible is True
    assert eligible.mode == "REDACTED_DERIVATIVE_ONLY"
    assert eligible.decision_id == decision.id


def test_deletion_requires_all_current_store_acks_and_fences_active_jobs(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Deletion owner")
    jobs = JobService(db_session)
    accepted = jobs.accept_job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        idempotency_key="deletion-job",
        request_hash="d" * 64,
        analysis_input_version="analysis-v1",
    )
    jobs.mark_dispatch_enqueued(owner_user_id=owner.id, job_id=accepted.job.id)
    lease = jobs.claim_execution(owner_user_id=owner.id, job_id=accepted.job.id)
    assert lease is not None

    deletion = DeletionOrchestrationService(db_session)
    preview = deletion.create_account_deletion_preview(
        owner_user_id=owner.id, preview_token="one-time-deletion-token"
    )
    request = deletion.confirm_and_start_account_deletion(
        owner_user_id=owner.id,
        deletion_request_id=preview.request.id,
        preview_token=preview.preview_token,
    )
    targets = list(
        db_session.scalars(
            select(DeletionTarget)
            .where(DeletionTarget.deletion_request_id == request.id)
            .order_by(DeletionTarget.store_type)
        )
    )
    assert request.status == "RUNNING"
    assert owner.deletion_epoch == 1
    assert accepted.job.execution_fence == lease.execution_fence + 1
    assert accepted.job.status == "CANCEL_REQUESTED"
    assert {target.store_type for target in targets} == {
        "POSTGRESQL",
        "NEO4J",
        "VECTOR",
        "CACHE",
        "CHECKPOINT",
    }
    assert len(targets) == 5
    messages = list(
        db_session.scalars(
            select(OutboxMessage).where(OutboxMessage.deletion_request_id == request.id)
        )
    )
    assert len(messages) == 5
    assert all(message.visibility_scope == "PRIVATE" for message in messages)
    assert {message.payload["target_type"] for message in messages} == {
        target.store_type for target in targets
    }
    assert all(UUID(message.payload["command_id"]) == message.id for message in messages)

    with pytest.raises(StaleDeletionAcknowledgementError):
        with db_session.begin_nested():
            deletion.acknowledge_target(
                owner_user_id=owner.id,
                deletion_request_id=request.id,
                deletion_target_id=targets[0].id,
                ack_epoch=0,
                ack_event_id=uuid4(),
            )
    failed_target = targets[0]
    deletion.record_target_failure(
        owner_user_id=owner.id,
        deletion_request_id=request.id,
        deletion_target_id=failed_target.id,
        failure_code="STORE_TIMEOUT",
    )
    retried = deletion.retry_target(
        owner_user_id=owner.id,
        deletion_request_id=request.id,
        deletion_target_id=failed_target.id,
    )
    assert retried.attempts == 2
    deletion.mark_target_dispatched(
        owner_user_id=owner.id,
        deletion_request_id=request.id,
        deletion_target_id=failed_target.id,
    )
    for target in targets:
        request = deletion.acknowledge_target(
            owner_user_id=owner.id,
            deletion_request_id=request.id,
            deletion_target_id=target.id,
            ack_epoch=1,
            ack_event_id=uuid4(),
        )
    db_session.commit()

    assert request.status == "COMPLETED"
    assert request.completed_at is not None
    assert all(target.status == "ACKNOWLEDGED" and target.ack_epoch == 1 for target in targets)


@pytest.mark.w1_isolated_commit_gate
def test_deletion_with_commit_operation_keeps_public_source_and_other_owner_unchanged(
    db_session: Session,
) -> None:
    """A private deletion creates only owner-scoped targets and an ABORT gate command."""

    owner = _create_owner(db_session, "Commit gate deletion owner")
    other_owner = _create_owner(db_session, "Unaffected owner")
    company = Company(legal_name="Public source company", display_name="Public source company")
    db_session.add(company)
    db_session.flush()
    source = Source(
        company_id=company.id,
        source_type="OFFICIAL",
        canonical_url="https://public.example/delete-safety",
        canonical_url_hash="delete-safety-source",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    db_session.add(source)

    jobs = JobService(db_session)
    accepted = jobs.accept_job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        idempotency_key="commit-gate-delete-job",
        request_hash="g" * 64,
        analysis_input_version="analysis-v1",
    )
    assert accepted.command is not None
    operation = W2CommitGateService(db_session).create_prepare_operation(
        owner_user_id=owner.id,
        job_id=accepted.job.id,
        command_id=accepted.command.id,
        execution_fence=accepted.job.execution_fence,
        owner_deletion_epoch=accepted.job.owner_deletion_epoch,
        result_digest="sha256:" + "b" * 64,
    )

    deletion = DeletionOrchestrationService(db_session)
    preview = deletion.create_account_deletion_preview(
        owner_user_id=owner.id,
        preview_token="commit-gate-private-target-token",
    )
    request = deletion.confirm_and_start_account_deletion(
        owner_user_id=owner.id,
        deletion_request_id=preview.request.id,
        preview_token=preview.preview_token,
    )
    targets = list(
        db_session.scalars(
            select(DeletionTarget).where(DeletionTarget.deletion_request_id == request.id)
        )
    )
    db_session.commit()

    stored_operation = db_session.get(W2CommitOperation, operation.id)
    assert stored_operation is not None
    assert (stored_operation.state, stored_operation.operation_revision) == ("ABORT_PENDING", 2)
    assert all(target.resource_type == "OWNER_PRIVATE_SCOPE" for target in targets)
    assert all(target.resource_id == owner.id for target in targets)
    assert db_session.get(Source, source.id) is not None
    assert db_session.get(User, other_owner.id).deletion_epoch == 0
