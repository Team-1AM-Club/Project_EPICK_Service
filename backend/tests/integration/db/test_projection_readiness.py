from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.application_workspace import ApplicationProject, ApplicationProjectVersion
from app.models.experience import Episode
from app.models.identity import User
from app.models.jobs import OutboxMessage
from app.models.projection import ProjectionSyncState, SnapshotExclusion
from app.services.application_workspace import ApplicationWorkspaceService
from app.services.experience import ExperienceService
from app.services.projection_outbox import (
    ProjectionOutboxService,
    ProjectionValidationError,
)
from app.services.recommendations import RecommendationService


@pytest.fixture(autouse=True)
def clean_projection_readiness_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        # Projection rows intentionally have no FK to their external resource.
        # Clear public delivery artifacts explicitly so a previous test run cannot
        # make an atomic-rollback assertion observe unrelated rows.
        connection.execute(
            text("TRUNCATE inbox_receipts, projection_sync_states, outbox_messages CASCADE")
        )
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


def _create_owner(session: Session, display_name: str) -> User:
    owner = User(display_name=display_name, locale="ko-KR", timezone="Asia/Seoul")
    session.add(owner)
    session.flush()
    return owner


def _create_workspace_episode(
    session: Session, *, owner: User, suffix: str
) -> tuple[UUID, UUID, UUID, UUID]:
    workspace_service = ApplicationWorkspaceService(session)
    experience_service = ExperienceService(session)
    company = workspace_service.create_company(
        legal_name=f"Projection {suffix}", display_name=f"Projection {suffix}"
    )
    project = workspace_service.create_project(
        owner_user_id=owner.id,
        company_id=company.id,
        title=f"Projection project {suffix}",
        role_name="Backend engineer",
    )
    activity = experience_service.create_activity(
        owner_user_id=owner.id,
        title=f"Projection activity {suffix}",
    )
    episode = experience_service.create_episode(
        owner_user_id=owner.id,
        activity_id=activity.id,
        title=f"Projection episode {suffix}",
    )
    assert episode.current_version_id is not None
    return company.id, project.id, activity.id, episode.id


def _insert_source(session: Session, *, company_id: UUID, suffix: str) -> tuple[UUID, UUID]:
    source_id = uuid4()
    source_version_id = uuid4()
    session.execute(
        text(
            "INSERT INTO sources ("
            "id, company_id, source_type, canonical_url, canonical_url_hash, "
            "url_normalization_version, policy_version, policy_checked_at"
            ") VALUES ("
            ":id, :company_id, 'CAREERS', :url, :url_hash, 'v1', 'policy-v1', now()"
            ")"
        ),
        {
            "id": source_id,
            "company_id": company_id,
            "url": f"https://projection.example.test/{suffix}",
            "url_hash": f"projection-url-{suffix}",
        },
    )
    session.execute(
        text(
            "INSERT INTO source_versions ("
            "id, source_id, company_id, version_no, collected_at, content_hash, "
            "parser_version, content_normalization_version, extraction_status, "
            "access_policy_at_collection, storage_policy_at_collection, "
            "reuse_policy_at_collection, policy_version_at_collection"
            ") VALUES ("
            ":id, :source_id, :company_id, 1, now(), :content_hash, "
            "'parser-v1', 'normalization-v1', 'SUCCEEDED', 'ALLOWED', "
            "'FULL_CONTENT_ALLOWED', 'CROSS_USER_ALLOWED', 'policy-v1'"
            ")"
        ),
        {
            "id": source_version_id,
            "source_id": source_id,
            "company_id": company_id,
            "content_hash": f"projection-content-{suffix}",
        },
    )
    return source_id, source_version_id


def _create_role(
    session: Session, *, company_id: UUID, suffix: str
) -> tuple[UUID, UUID]:
    _, source_version_id = _insert_source(session, company_id=company_id, suffix=f"role-{suffix}")
    evidence_span_id = uuid4()
    role_id = uuid4()
    role_version_id = uuid4()
    session.execute(
        text(
            "INSERT INTO evidence_spans ("
            "id, source_version_id, excerpt, locator_type, locator, chunk_order, excerpt_hash"
            ") VALUES (:id, :source_version_id, 'Role evidence', 'LINE_RANGE', '1-1', 0, "
            ":excerpt_hash)"
        ),
        {
            "id": evidence_span_id,
            "source_version_id": source_version_id,
            "excerpt_hash": f"role-evidence-{suffix}",
        },
    )
    session.execute(
        text("INSERT INTO roles (id, company_id) VALUES (:id, :company_id)"),
        {"id": role_id, "company_id": company_id},
    )
    session.execute(
        text(
            "INSERT INTO role_versions ("
            "id, role_id, company_id, version_no, name, normalized_name, evidence_span_id"
            ") VALUES (:id, :role_id, :company_id, 1, :name, :normalized_name, :evidence_span_id)"
        ),
        {
            "id": role_version_id,
            "role_id": role_id,
            "company_id": company_id,
            "name": f"Role {suffix}",
            "normalized_name": f"role-{suffix}",
            "evidence_span_id": evidence_span_id,
        },
    )
    session.execute(
        text("UPDATE roles SET current_version_id = :version_id WHERE id = :id"),
        {"version_id": role_version_id, "id": role_id},
    )
    return role_id, role_version_id


def _append_project_version_with_role(
    session: Session, *, project_id: UUID, role_version_id: UUID
) -> None:
    project = session.get(ApplicationProject, project_id)
    assert project is not None and project.current_version_id is not None
    current = session.get(ApplicationProjectVersion, project.current_version_id)
    assert current is not None
    version = ApplicationProjectVersion(
        project_id=current.project_id,
        owner_user_id=current.owner_user_id,
        version_no=current.version_no + 1,
        company_id=current.company_id,
        title=current.title,
        season=current.season,
        organization_name=current.organization_name,
        role_name=current.role_name,
        role_version_id=role_version_id,
        change_reason="Attach the resolved public role.",
    )
    session.add(version)
    session.flush()
    project.current_version_id = version.id
    project.lock_version += 1
    session.flush()


def test_source_mutation_and_projection_outbox_rollback_together(db_session: Session) -> None:
    owner = _create_owner(db_session, "Projection atomic owner")
    company_id, _, _, _ = _create_workspace_episode(db_session, owner=owner, suffix="atomic")
    source_id, source_version_id = _insert_source(
        db_session, company_id=company_id, suffix="atomic"
    )
    db_session.commit()

    db_session.execute(
        text("UPDATE sources SET last_collection_status = 'SUCCEEDED' WHERE id = :source_id"),
        {"source_id": source_id},
    )
    event = ProjectionOutboxService(db_session).stage_public_projection(
        resource_type="SOURCE",
        resource_id=source_id,
        resource_version=source_version_id,
        projection_type="GRAPH",
        aggregate_type="SOURCE",
        aggregate_id=source_id,
        aggregate_revision=1,
        message_type="projection.source.changed",
        payload={"source_id": str(source_id), "source_version_id": str(source_version_id)},
    )
    db_session.flush()
    assert event.visibility_scope == "PUBLIC"
    assert db_session.scalar(select(ProjectionSyncState.id)) is not None
    db_session.rollback()

    assert db_session.scalar(
        text("SELECT last_collection_status FROM sources WHERE id = :source_id"),
        {"source_id": source_id},
    ) == "NEVER_COLLECTED"
    assert db_session.scalar(select(OutboxMessage.id).where(OutboxMessage.id == event.id)) is None
    assert db_session.scalar(select(ProjectionSyncState.id)) is None


def test_final_public_outbox_failure_is_not_claimed_again(db_session: Session) -> None:
    service = ProjectionOutboxService(db_session)
    event = service.stage_public_projection(
        resource_type="SOURCE",
        resource_id=uuid4(),
        resource_version=uuid4(),
        projection_type="GRAPH",
        aggregate_type="SOURCE",
        aggregate_id=uuid4(),
        aggregate_revision=1,
        message_type="projection.source.changed",
        payload={"source_id": str(uuid4()), "source_version_id": str(uuid4())},
    )
    assert [message.id for message in service.claim_public_outbox_messages(limit=1)] == [event.id]
    assert service.mark_public_outbox_failed_final(event_id=event.id).status == "FAILED_FINAL"
    db_session.commit()

    assert service.claim_public_outbox_messages(limit=1) == []


def test_public_outbox_rejects_private_payload_and_private_references(db_session: Session) -> None:
    service = ProjectionOutboxService(db_session)
    with pytest.raises(ProjectionValidationError, match="private"):
        service.stage_public_projection(
            resource_type="SOURCE",
            resource_id=uuid4(),
            resource_version=uuid4(),
            projection_type="GRAPH",
            aggregate_type="SOURCE",
            aggregate_id=uuid4(),
            aggregate_revision=1,
            message_type="projection.source.changed",
            payload={"owner_user_id": str(uuid4())},
        )

    db_session.add(
        OutboxMessage(
            message_type="projection.source.changed",
            schema_version="1.0",
            visibility_scope="PUBLIC",
            aggregate_type="SOURCE",
            aggregate_id=uuid4(),
            aggregate_revision=1,
            payload={"job_id": str(uuid4())},
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_redelivery_dedup_and_late_projection_revision_do_not_regress_state(
    db_session: Session,
) -> None:
    service = ProjectionOutboxService(db_session)
    resource_id = uuid4()
    resource_version = uuid4()
    event = service.stage_public_projection(
        resource_type="SOURCE",
        resource_id=resource_id,
        resource_version=resource_version,
        projection_type="GRAPH",
        aggregate_type="SOURCE",
        aggregate_id=resource_id,
        aggregate_revision=5,
        message_type="projection.source.changed",
        payload={"source_id": str(resource_id), "source_version_id": str(resource_version)},
    )
    db_session.commit()

    first_claim = service.claim_public_outbox_messages(limit=1)
    assert [message.id for message in first_claim] == [event.id]
    assert first_claim[0].status == "PUBLISHING"
    assert first_claim[0].attempts == 1
    service.mark_public_outbox_retryable(event_id=event.id, available_at=datetime.now(UTC))
    db_session.commit()

    second_claim = service.claim_public_outbox_messages(limit=1)
    assert [message.id for message in second_claim] == [event.id]
    assert second_claim[0].attempts == 2
    assert service.record_inbox_receipt(
        consumer_name="projection-test-consumer", event_id=event.id, outcome_code="APPLIED"
    )
    assert not service.record_inbox_receipt(
        consumer_name="projection-test-consumer", event_id=event.id, outcome_code="APPLIED"
    )
    service.mark_public_outbox_published(event_id=event.id)
    state = service.record_projection_outcome(
        resource_type="SOURCE",
        resource_id=resource_id,
        resource_version=resource_version,
        projection_type="GRAPH",
        event_id=event.id,
        aggregate_revision=5,
        status="SYNCED",
    )
    db_session.commit()

    late_state = service.record_projection_outcome(
        resource_type="SOURCE",
        resource_id=resource_id,
        resource_version=resource_version,
        projection_type="GRAPH",
        event_id=uuid4(),
        aggregate_revision=4,
        status="ERROR",
        error_code="LATE_EVENT",
    )
    db_session.commit()

    assert state.status == "SYNCED"
    assert late_state.status == "SYNCED"
    assert late_state.last_applied_revision == 5
    assert late_state.error_code is None


def test_projection_outage_preserves_source_change_and_records_retryable_state(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Projection outage owner")
    company_id, _, _, _ = _create_workspace_episode(db_session, owner=owner, suffix="outage")
    source_id, source_version_id = _insert_source(
        db_session, company_id=company_id, suffix="outage"
    )
    db_session.commit()

    db_session.execute(
        text("UPDATE sources SET last_collection_status = 'PARTIAL' WHERE id = :source_id"),
        {"source_id": source_id},
    )
    event = ProjectionOutboxService(db_session).stage_public_projection(
        resource_type="SOURCE",
        resource_id=source_id,
        resource_version=source_version_id,
        projection_type="VECTOR",
        aggregate_type="SOURCE",
        aggregate_id=source_id,
        aggregate_revision=2,
        message_type="projection.source.changed",
        payload={"source_id": str(source_id), "source_version_id": str(source_version_id)},
    )
    state = ProjectionOutboxService(db_session).record_projection_outcome(
        resource_type="SOURCE",
        resource_id=source_id,
        resource_version=source_version_id,
        projection_type="VECTOR",
        event_id=event.id,
        aggregate_revision=2,
        status="ERROR",
        error_code="PROJECTION_UNAVAILABLE",
    )
    db_session.commit()

    assert db_session.scalar(
        text("SELECT last_collection_status FROM sources WHERE id = :source_id"),
        {"source_id": source_id},
    ) == "PARTIAL"
    assert state.status == "ERROR"
    assert state.error_code == "PROJECTION_UNAVAILABLE"


def test_snapshot_captures_only_active_owner_exclusions_and_keeps_history(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Snapshot exclusion owner")
    _, project_id, _, episode_id = _create_workspace_episode(
        db_session, owner=owner, suffix="snapshot-exclusion"
    )
    episode = db_session.get(Episode, episode_id)
    assert episode is not None and episode.current_version_id is not None
    exclusion_service = ProjectionOutboxService(db_session)
    exclusion = exclusion_service.create_experience_exclusion(
        owner_user_id=owner.id,
        episode_id=episode.id,
        scope="GLOBAL",
        reason="Not relevant to this application.",
    )
    snapshot = RecommendationService(db_session).create_snapshot(
        owner_user_id=owner.id,
        project_id=project_id,
        episode_version_ids=(episode.current_version_id,),
    )
    db_session.commit()

    frozen = db_session.scalars(
        select(SnapshotExclusion).where(SnapshotExclusion.snapshot_id == snapshot.id)
    ).all()
    assert [(item.exclusion_id, item.owner_user_id) for item in frozen] == [
        (exclusion.id, owner.id)
    ]

    exclusion_service.revoke_experience_exclusion(
        owner_user_id=owner.id, exclusion_id=exclusion.id
    )
    db_session.commit()

    frozen_after_revoke = db_session.scalars(
        select(SnapshotExclusion).where(SnapshotExclusion.snapshot_id == snapshot.id)
    ).all()
    assert [item.exclusion_id for item in frozen_after_revoke] == [exclusion.id]


def test_snapshot_fixes_only_exclusions_matching_its_project_context(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Scoped snapshot exclusion owner")
    company_id, project_id, activity_id, episode_id = _create_workspace_episode(
        db_session, owner=owner, suffix="scoped-main"
    )
    other_company_id, other_project_id, _, _ = _create_workspace_episode(
        db_session, owner=owner, suffix="scoped-other"
    )
    episode = db_session.get(Episode, episode_id)
    assert episode is not None and episode.current_version_id is not None

    exclusion_service = ProjectionOutboxService(db_session)
    included = [
        exclusion_service.create_experience_exclusion(
            owner_user_id=owner.id, activity_id=activity_id, scope="GLOBAL"
        ),
        exclusion_service.create_experience_exclusion(
            owner_user_id=owner.id,
            activity_id=activity_id,
            scope="COMPANY",
            company_id=company_id,
        ),
        exclusion_service.create_experience_exclusion(
            owner_user_id=owner.id,
            activity_id=activity_id,
            scope="PROJECT",
            project_id=project_id,
        ),
    ]
    excluded = [
        exclusion_service.create_experience_exclusion(
            owner_user_id=owner.id,
            activity_id=activity_id,
            scope="COMPANY",
            company_id=other_company_id,
        ),
        exclusion_service.create_experience_exclusion(
            owner_user_id=owner.id,
            activity_id=activity_id,
            scope="PROJECT",
            project_id=other_project_id,
        ),
    ]

    snapshot = RecommendationService(db_session).create_snapshot(
        owner_user_id=owner.id,
        project_id=project_id,
        episode_version_ids=(episode.current_version_id,),
    )
    db_session.commit()

    frozen_ids = set(
        db_session.scalars(
            select(SnapshotExclusion.exclusion_id).where(
                SnapshotExclusion.snapshot_id == snapshot.id
            )
        )
    )
    assert frozen_ids == {exclusion.id for exclusion in included}
    assert not frozen_ids.intersection(exclusion.id for exclusion in excluded)

    db_session.add(
        SnapshotExclusion(
            snapshot_id=snapshot.id,
            exclusion_id=excluded[0].id,
            owner_user_id=owner.id,
        )
    )
    with pytest.raises(IntegrityError, match="scope does not match"):
        db_session.flush()
    db_session.rollback()


def test_snapshot_fixes_only_the_exclusion_for_its_resolved_role(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Role-scoped exclusion owner")
    company_id, project_id, activity_id, episode_id = _create_workspace_episode(
        db_session, owner=owner, suffix="role-scoped"
    )
    matching_role_id, matching_role_version_id = _create_role(
        db_session, company_id=company_id, suffix="matching"
    )
    other_role_id, _ = _create_role(db_session, company_id=company_id, suffix="other")
    _append_project_version_with_role(
        db_session, project_id=project_id, role_version_id=matching_role_version_id
    )
    episode = db_session.get(Episode, episode_id)
    assert episode is not None and episode.current_version_id is not None

    exclusion_service = ProjectionOutboxService(db_session)
    matching_exclusion = exclusion_service.create_experience_exclusion(
        owner_user_id=owner.id,
        activity_id=activity_id,
        scope="ROLE",
        role_id=matching_role_id,
    )
    other_exclusion = exclusion_service.create_experience_exclusion(
        owner_user_id=owner.id,
        activity_id=activity_id,
        scope="ROLE",
        role_id=other_role_id,
    )

    snapshot = RecommendationService(db_session).create_snapshot(
        owner_user_id=owner.id,
        project_id=project_id,
        episode_version_ids=(episode.current_version_id,),
    )
    db_session.commit()

    frozen_ids = set(
        db_session.scalars(
            select(SnapshotExclusion.exclusion_id).where(
                SnapshotExclusion.snapshot_id == snapshot.id
            )
        )
    )
    assert frozen_ids == {matching_exclusion.id}

    db_session.add(
        SnapshotExclusion(
            snapshot_id=snapshot.id,
            exclusion_id=other_exclusion.id,
            owner_user_id=owner.id,
        )
    )
    with pytest.raises(IntegrityError, match="scope does not match"):
        db_session.flush()
    db_session.rollback()
