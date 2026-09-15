from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.application_workspace import ApplicationProject, ProjectQuestion
from app.models.experience import Episode
from app.models.identity import User
from app.models.recommendations import (
    MaterialSelectionItem,
    MaterialSelectionSet,
    RecommendationCandidate,
    RecommendationRun,
    SnapshotEpisodeVersion,
)
from app.services.application_workspace import ApplicationWorkspaceService
from app.services.experience import ExperienceService
from app.services.jobs import JobInputReference, JobService
from app.services.recommendations import (
    RecommendationNotFoundError,
    RecommendationService,
    RecommendationValidationError,
)


@pytest.fixture(autouse=True)
def clean_recommendation_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


@dataclass(frozen=True)
class RecommendationWorkspace:
    owner: User
    project: ApplicationProject
    question: ProjectQuestion
    episode_ids: tuple


def _create_owner(db_session: Session, display_name: str) -> User:
    owner = User(display_name=display_name, locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(owner)
    db_session.flush()
    return owner


def _create_workspace(
    db_session: Session, *, owner: User, suffix: str, episode_count: int = 1
) -> RecommendationWorkspace:
    workspace_service = ApplicationWorkspaceService(db_session)
    experience_service = ExperienceService(db_session)
    company = workspace_service.create_company(
        legal_name=f"EPICK {suffix}", display_name=f"EPICK {suffix}"
    )
    project = workspace_service.create_project(
        owner_user_id=owner.id,
        company_id=company.id,
        title=f"Backend application {suffix}",
        role_name="Backend engineer",
    )
    question = workspace_service.create_question(
        owner_user_id=owner.id,
        project_id=project.id,
        display_order=0,
        prompt="Which experience shows the best fit?",
        source="USER",
    )
    activity = experience_service.create_activity(
        owner_user_id=owner.id,
        title=f"Service project {suffix}",
    )
    episode_ids = []
    for index in range(episode_count):
        episode = experience_service.create_episode(
            owner_user_id=owner.id,
            activity_id=activity.id,
            title=f"Episode {suffix}-{index + 1}",
        )
        assert episode.current_version_id is not None
        episode_ids.append(episode.current_version_id)
    return RecommendationWorkspace(
        owner=owner,
        project=project,
        question=question,
        episode_ids=tuple(episode_ids),
    )


def test_snapshot_freezes_versions_and_supports_a_job_snapshot_input(db_session: Session) -> None:
    owner = _create_owner(db_session, "Snapshot owner")
    workspace = _create_workspace(db_session, owner=owner, suffix="snapshot")
    recommendation_service = RecommendationService(db_session)
    snapshot = recommendation_service.create_snapshot(
        owner_user_id=owner.id,
        project_id=workspace.project.id,
        episode_version_ids=workspace.episode_ids,
        recommendation_policy_version="1.0",
    )
    db_session.commit()

    original_project_version_id = snapshot.project_version_id
    original_episode_version_id = workspace.episode_ids[0]
    workspace_service = ApplicationWorkspaceService(db_session)
    experience_service = ExperienceService(db_session)
    workspace_service.append_project_version(
        owner_user_id=owner.id,
        project_id=workspace.project.id,
        expected_lock_version=workspace.project.lock_version,
        title="Updated backend application",
    )
    episode = db_session.scalar(
        select(Episode).where(Episode.current_version_id == original_episode_version_id)
    )
    assert episode is not None
    experience_service.append_episode_version(
        owner_user_id=owner.id,
        episode_id=episode.id,
        expected_lock_version=episode.lock_version,
        title="Updated episode",
    )
    db_session.commit()

    snapshot_items = db_session.scalars(
        select(SnapshotEpisodeVersion).where(SnapshotEpisodeVersion.snapshot_id == snapshot.id)
    ).all()
    db_session.refresh(workspace.project)
    assert snapshot.status == "READY"
    assert snapshot.project_version_id == original_project_version_id
    assert [item.episode_version_id for item in snapshot_items] == [original_episode_version_id]
    assert workspace.project.active_snapshot_id == snapshot.id

    acceptance = JobService(db_session).accept_job(
        owner_user_id=owner.id,
        job_type="MATERIAL_SELECTION",
        idempotency_key="snapshot-input",
        request_hash="a" * 64,
        input_refs=(JobInputReference(snapshot_id=snapshot.id),),
    )
    db_session.commit()
    assert acceptance.job.id is not None


def test_candidate_must_use_the_run_snapshot_and_cross_project_combinations_fail_at_db_boundary(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Boundary owner")
    first = _create_workspace(db_session, owner=owner, suffix="first")
    second = _create_workspace(db_session, owner=owner, suffix="second")
    recommendation_service = RecommendationService(db_session)
    first_snapshot = recommendation_service.create_snapshot(
        owner_user_id=owner.id,
        project_id=first.project.id,
        episode_version_ids=first.episode_ids,
    )
    second_snapshot = recommendation_service.create_snapshot(
        owner_user_id=owner.id,
        project_id=second.project.id,
        episode_version_ids=second.episode_ids,
    )
    first_run = recommendation_service.create_recommendation_run(
        owner_user_id=owner.id,
        project_id=first.project.id,
        question_id=first.question.id,
        snapshot_id=first_snapshot.id,
        analysis_policy_version="1.0",
        requested_candidate_limit=3,
    )
    db_session.commit()

    invalid_run = RecommendationRun(
        owner_user_id=owner.id,
        project_id=second.project.id,
        question_id=second.question.id,
        question_version_id=second.question.current_version_id,
        snapshot_id=first_snapshot.id,
        analysis_policy_version="1.0",
        requested_candidate_limit=3,
    )
    db_session.add(invalid_run)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    invalid_candidate = RecommendationCandidate(
        owner_user_id=owner.id,
        run_id=first_run.id,
        question_id=first.question.id,
        snapshot_id=first_snapshot.id,
        candidate_no=1,
        episode_version_id=second.episode_ids[0],
        match_status="DIRECT_MATCH",
        short_reason="A different snapshot cannot be used.",
        validation_status="PENDING",
        result_version="1.0",
    )
    db_session.add(invalid_candidate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    with pytest.raises(RecommendationValidationError):
        recommendation_service.record_candidate(
            owner_user_id=owner.id,
            run_id=first_run.id,
            episode_version_id=second.episode_ids[0],
            match_status="DIRECT_MATCH",
            short_reason="The service rejects the bad link before flush.",
            result_version="1.0",
        )

    assert second_snapshot.project_id == second.project.id


def test_material_selection_is_atomic_versioned_and_prevents_candidate_deletion(
    db_session: Session,
) -> None:
    owner = _create_owner(db_session, "Selection owner")
    workspace = _create_workspace(db_session, owner=owner, suffix="selection", episode_count=2)
    recommendation_service = RecommendationService(db_session)
    snapshot = recommendation_service.create_snapshot(
        owner_user_id=owner.id,
        project_id=workspace.project.id,
        episode_version_ids=workspace.episode_ids,
    )
    run = recommendation_service.create_recommendation_run(
        owner_user_id=owner.id,
        project_id=workspace.project.id,
        question_id=workspace.question.id,
        snapshot_id=snapshot.id,
        analysis_policy_version="1.0",
        requested_candidate_limit=2,
    )
    first_candidate = recommendation_service.record_candidate(
        owner_user_id=owner.id,
        run_id=run.id,
        episode_version_id=workspace.episode_ids[0],
        match_status="DIRECT_MATCH",
        short_reason="Directly demonstrates the requested work.",
        result_version="1.0",
        internal_rank=1,
    )
    second_candidate = recommendation_service.record_candidate(
        owner_user_id=owner.id,
        run_id=run.id,
        episode_version_id=workspace.episode_ids[1],
        match_status="PARTIAL_RELEVANCE",
        short_reason="Provides adjacent relevant evidence.",
        result_version="1.0",
        internal_rank=2,
    )
    first_selection = recommendation_service.replace_material_selection(
        owner_user_id=owner.id,
        project_id=workspace.project.id,
        question_id=workspace.question.id,
        run_id=run.id,
        candidate_ids=(first_candidate.id,),
    )
    db_session.commit()

    second_selection = recommendation_service.replace_material_selection(
        owner_user_id=owner.id,
        project_id=workspace.project.id,
        question_id=workspace.question.id,
        run_id=run.id,
        candidate_ids=(second_candidate.id, first_candidate.id),
    )
    db_session.commit()

    selection_sets = db_session.scalars(
        select(MaterialSelectionSet)
        .where(MaterialSelectionSet.question_id == workspace.question.id)
        .order_by(MaterialSelectionSet.selected_at)
    ).all()
    current_items = db_session.scalars(
        select(MaterialSelectionItem)
        .where(MaterialSelectionItem.selection_set_id == second_selection.id)
        .order_by(MaterialSelectionItem.selection_order)
    ).all()
    db_session.refresh(workspace.project)
    assert first_selection.is_current is False
    assert first_selection.superseded_at is not None
    assert [selection_set.is_current for selection_set in selection_sets] == [False, True]
    assert [item.candidate_id for item in current_items] == [
        second_candidate.id,
        first_candidate.id,
    ]
    assert workspace.project.status == "MATERIALS_SELECTED"

    db_session.delete(second_candidate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_selection_rejects_empty_or_foreign_candidate_lists(db_session: Session) -> None:
    owner = _create_owner(db_session, "Selection validation owner")
    workspace = _create_workspace(db_session, owner=owner, suffix="validation")
    recommendation_service = RecommendationService(db_session)
    snapshot = recommendation_service.create_snapshot(
        owner_user_id=owner.id,
        project_id=workspace.project.id,
        episode_version_ids=workspace.episode_ids,
    )
    run = recommendation_service.create_recommendation_run(
        owner_user_id=owner.id,
        project_id=workspace.project.id,
        question_id=workspace.question.id,
        snapshot_id=snapshot.id,
        analysis_policy_version="1.0",
        requested_candidate_limit=1,
    )
    with pytest.raises(RecommendationValidationError):
        recommendation_service.replace_material_selection(
            owner_user_id=owner.id,
            project_id=workspace.project.id,
            question_id=workspace.question.id,
            run_id=run.id,
            candidate_ids=(),
        )
    with pytest.raises(RecommendationNotFoundError):
        recommendation_service.replace_material_selection(
            owner_user_id=owner.id,
            project_id=workspace.project.id,
            question_id=workspace.question.id,
            run_id=run.id,
            candidate_ids=(workspace.episode_ids[0],),
        )


def test_snapshot_and_selection_tables_enforce_owner_rls(migrated_engine: Engine) -> None:
    table_names = (
        "project_snapshots",
        "snapshot_episode_versions",
        "recommendation_runs",
        "recommendation_candidates",
        "material_selection_sets",
        "material_selection_items",
    )
    with migrated_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = ANY(:table_names)"
                ),
                {"table_names": list(table_names)},
            )
            .mappings()
            .all()
        )

    assert {row["relname"] for row in rows} == set(table_names)
    assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows)
