from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.application_workspace import ApplicationProject, ProjectQuestion
from app.models.identity import User
from app.models.recommendation_execution import (
    RecommendationExecutionBinding,
    RecommendationExecutionEpisode,
    RecommendationPublication,
)
from app.models.recommendations import ProjectSnapshot, RecommendationCandidate, RecommendationRun
from app.services.application_workspace import ApplicationWorkspaceService
from app.services.experience import ExperienceService
from app.services.recommendations import RecommendationService
from app.services.w4_recommendation_run_store import (
    W4RecommendationRunStoreService,
    W4RecommendationStoreError,
    canonical_sha256,
)

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def clean_recommendation_execution_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


@dataclass(frozen=True)
class ExecutionFixture:
    owner_id: UUID
    project_id: UUID
    question_id: UUID
    question_version_id: UUID
    snapshot_id: UUID
    run_id: UUID
    binding_id: UUID
    episode_version_id: UUID
    source_id: UUID
    source_version_id: UUID


def _seed_execution(session: Session, *, candidate_limit: int = 2) -> ExecutionFixture:
    owner = User(display_name="W4 recommendation owner", locale="ko-KR", timezone="Asia/Seoul")
    session.add(owner)
    session.flush()
    workspace = ApplicationWorkspaceService(session)
    experience = ExperienceService(session)
    company = workspace.create_company(
        legal_name=f"W4 Recommendation {uuid4()}",
        display_name="W4 Recommendation",
    )
    project = workspace.create_project(
        owner_user_id=owner.id,
        company_id=company.id,
        title="W4 recommendation acceptance",
        role_name="Backend engineer",
    )
    question = workspace.create_question(
        owner_user_id=owner.id,
        project_id=project.id,
        display_order=0,
        prompt="Explain a relevant project.",
        source="USER",
    )
    activity = experience.create_activity(owner_user_id=owner.id, title="W4 activity")
    episode = experience.create_episode(
        owner_user_id=owner.id,
        activity_id=activity.id,
        title="W4 episode",
    )
    assert episode.current_version_id is not None
    recommendation = RecommendationService(session)
    snapshot = recommendation.create_snapshot(
        owner_user_id=owner.id,
        project_id=project.id,
        episode_version_ids=(episode.current_version_id,),
    )
    run = recommendation.create_recommendation_run(
        owner_user_id=owner.id,
        project_id=project.id,
        question_id=question.id,
        snapshot_id=snapshot.id,
        analysis_policy_version="w4-synthetic-acceptance-v1",
        requested_candidate_limit=candidate_limit,
    )
    run.result_origin = "ENGINE"
    context = {"context_version": f"w1-engine-{run.id}", "data_kind": "SYNTHETIC"}
    binding = RecommendationExecutionBinding(
        run_id=run.id,
        owner_user_id=owner.id,
        project_id=project.id,
        question_id=question.id,
        question_version_id=question.current_version_id,
        snapshot_id=snapshot.id,
        owner_deletion_epoch=owner.deletion_epoch,
        context_sha256=canonical_sha256(context),
        contract_version="w1-w4-recommendation/1.0",
        engine_source_revision="df41433218918e4167784243dc9b88e5a858278d",
        request_body={"schema_version": "w4-service-input/0.1"},
        context_body=context,
    )
    session.add(binding)
    session.flush()
    session.add(
        RecommendationExecutionEpisode(
            binding_id=binding.id,
            episode_version_id=episode.current_version_id,
            owner_user_id=owner.id,
            episode_id=str(episode.id),
            episode_version=1,
        )
    )
    source_id = uuid4()
    source_version_id = uuid4()
    session.execute(
        text(
            "INSERT INTO sources (id, company_id, source_type, canonical_url, "
            "canonical_url_hash, url_normalization_version, policy_version, policy_checked_at) "
            "VALUES (:source_id, :company_id, 'CAREERS', :url, :url_hash, 'v1', 'v1', now())"
        ),
        {
            "source_id": source_id,
            "company_id": company.id,
            "url": f"https://w4.example.test/{source_id}",
            "url_hash": f"w4-{source_id}",
        },
    )
    session.execute(
        text(
            "INSERT INTO source_versions (id, source_id, company_id, version_no, collected_at, "
            "content_hash, parser_version, content_normalization_version, extraction_status, "
            "access_policy_at_collection, storage_policy_at_collection, "
            "reuse_policy_at_collection, policy_version_at_collection) "
            "VALUES (:version_id, :source_id, :company_id, 1, now(), :content_hash, "
            "'v1', 'v1', 'SUCCEEDED', 'ALLOWED', 'FULL_CONTENT_ALLOWED', "
            "'CROSS_USER_ALLOWED', 'v1')"
        ),
        {
            "version_id": source_version_id,
            "source_id": source_id,
            "company_id": company.id,
            "content_hash": f"content-{source_id}",
        },
    )
    session.execute(
        text("UPDATE sources SET current_version_id=:version_id WHERE id=:source_id"),
        {"version_id": source_version_id, "source_id": source_id},
    )
    session.commit()
    assert question.current_version_id is not None
    return ExecutionFixture(
        owner_id=owner.id,
        project_id=project.id,
        question_id=question.id,
        question_version_id=question.current_version_id,
        snapshot_id=snapshot.id,
        run_id=run.id,
        binding_id=binding.id,
        episode_version_id=episode.current_version_id,
        source_id=source_id,
        source_version_id=source_version_id,
    )


def _publication(
    fixture: ExecutionFixture,
    *,
    candidate_ids: tuple[UUID, ...] | None = None,
    with_source: bool = False,
) -> dict[str, object]:
    ids = candidate_ids or (fixture.episode_version_id,)
    dependencies: list[dict[str, object]] = []
    if with_source:
        dependencies.append(
            {
                "source_id": str(fixture.source_id),
                "source_version_id": str(fixture.source_version_id),
            }
        )
    return {
        "schema_version": "w4-w1-publication/0.1-draft",
        "run_id": str(fixture.run_id),
        "result_origin": "ENGINE",
        "input_data_kind": "SYNTHETIC",
        "run_status": "LIMITED",
        "result_status": "LIMITED",
        "limited_analysis": True,
        "limitations": ["W4_SYNTHETIC_ACCEPTANCE_ONLY"],
        "result_version": "w4-result-v1",
        "candidates": [
            {
                "episode_version_id": str(episode_version_id),
                "match_status": "DIRECT_MATCH",
                "short_reason": "Synthetic acceptance evidence",
                "strength_summary": "Version-pinned evidence",
                "limitation_summary": "Synthetic only",
                "validation_status": "LIMITED",
                "result_version": "w4-result-v1",
                "internal_rank": rank,
            }
            for rank, episode_version_id in enumerate(ids, start=1)
        ],
        "full_result": {"processing_status": "LIMITED"},
        "source_dependencies": dependencies,
    }


def _acquire(session: Session, fixture: ExecutionFixture) -> UUID:
    document = W4RecommendationRunStoreService(session, lease_seconds=300).acquire(
        owner_user_id=fixture.owner_id,
        run_id=fixture.run_id,
    )
    assert document is not None
    session.commit()
    return UUID(str(document["lease_token"]))


def test_competing_acquire_keeps_exactly_one_live_lease(migrated_engine: Engine) -> None:
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with factory() as seed:
        fixture = _seed_execution(seed)
    with factory() as first:
        lease_token = _acquire(first, fixture)
    with factory() as second:
        with pytest.raises(W4RecommendationStoreError, match="W4_RUN_BUSY"):
            W4RecommendationRunStoreService(second, lease_seconds=300).acquire(
                owner_user_id=fixture.owner_id,
                run_id=fixture.run_id,
            )
        second.rollback()
    with factory() as verify:
        binding = verify.get(RecommendationExecutionBinding, fixture.binding_id)
        assert binding is not None
        assert binding.lease_token == lease_token
        assert binding.attempt_no == 1


def test_duplicate_after_commit_is_acknowledged_without_duplicate_rows(
    migrated_engine: Engine,
) -> None:
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with factory() as seed:
        fixture = _seed_execution(seed)
    with factory() as session:
        lease = _acquire(session, fixture)
        service = W4RecommendationRunStoreService(session, lease_seconds=300)
        payload = _publication(fixture)
        assert service.publish(run_id=fixture.run_id, lease_token=lease, publication=payload)
        session.commit()
        assert service.publish(run_id=fixture.run_id, lease_token=lease, publication=payload)
        session.commit()
        assert session.scalar(select(func.count()).select_from(RecommendationPublication)) == 1
        assert session.scalar(select(func.count()).select_from(RecommendationCandidate)) == 1


def test_partial_candidate_failure_rolls_back_all_publication_rows(
    migrated_engine: Engine,
) -> None:
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with factory() as seed:
        fixture = _seed_execution(seed)
    with factory() as session:
        lease = _acquire(session, fixture)
        with pytest.raises(W4RecommendationStoreError, match="W4_RESULT_CANDIDATE_MISMATCH"):
            W4RecommendationRunStoreService(session, lease_seconds=300).publish(
                run_id=fixture.run_id,
                lease_token=lease,
                publication=_publication(
                    fixture,
                    candidate_ids=(fixture.episode_version_id, uuid4()),
                ),
            )
        session.rollback()
        assert session.scalar(select(func.count()).select_from(RecommendationPublication)) == 0
        assert session.scalar(select(func.count()).select_from(RecommendationCandidate)) == 0


@pytest.mark.parametrize(
    "stale_boundary",
    ("cancel", "owner_epoch", "question", "snapshot", "source"),
)
def test_publish_rejects_currentness_races(
    migrated_engine: Engine,
    stale_boundary: str,
) -> None:
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with factory() as seed:
        fixture = _seed_execution(seed)
    with factory() as session:
        lease = _acquire(session, fixture)
        if stale_boundary == "cancel":
            run = session.get(RecommendationRun, fixture.run_id)
            assert run is not None
            run.status = "CANCELLED"
        elif stale_boundary == "owner_epoch":
            owner = session.get(User, fixture.owner_id)
            assert owner is not None
            owner.deletion_epoch += 1
        elif stale_boundary == "question":
            question = session.get(ProjectQuestion, fixture.question_id)
            assert question is not None
            question.status = "ARCHIVED"
        elif stale_boundary == "snapshot":
            snapshot = session.get(ProjectSnapshot, fixture.snapshot_id)
            assert snapshot is not None
            snapshot.status = "STALE"
        else:
            session.execute(
                text("UPDATE sources SET current_version_id=NULL WHERE id=:source_id"),
                {"source_id": fixture.source_id},
            )
        session.commit()
        accepted = W4RecommendationRunStoreService(session, lease_seconds=300).publish(
            run_id=fixture.run_id,
            lease_token=lease,
            publication=_publication(fixture, with_source=stale_boundary == "source"),
        )
        assert accepted is False
        session.rollback()
        assert session.scalar(select(func.count()).select_from(RecommendationPublication)) == 0
        assert session.scalar(select(func.count()).select_from(RecommendationCandidate)) == 0
        project = session.get(ApplicationProject, fixture.project_id)
        assert project is not None
