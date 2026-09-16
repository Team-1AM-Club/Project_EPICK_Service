from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.services.recommendation_execution import SyntheticRecommendationAdapter


def create_recommendation_workspace(
    client: TestClient,
    *,
    company_id: UUID,
    suffix: str,
    episode_count: int = 1,
) -> dict[str, str]:
    activity = client.post(
        "/api/v1/activities",
        json={"title": f"추천 활동 {suffix}"},
        headers={"Idempotency-Key": f"recommendation-activity-{suffix}"},
    )
    assert activity.status_code == 201
    for number in range(episode_count):
        episode = client.post(
            f"/api/v1/activities/{activity.json()['id']}/episodes",
            json={"title": f"추천 에피소드 {suffix}-{number + 1}"},
            headers={"Idempotency-Key": f"recommendation-episode-{suffix}-{number}"},
        )
        assert episode.status_code == 201
    project = client.post(
        "/api/v1/application-projects",
        json={
            "title": f"추천 지원 {suffix}",
            "company_id": str(company_id),
            "role_name": "Backend Engineer",
        },
        headers={"Idempotency-Key": f"recommendation-project-{suffix}"},
    )
    assert project.status_code == 201
    question = client.post(
        f"/api/v1/application-projects/{project.json()['id']}/questions",
        json={"prompt": "문제를 해결한 경험을 설명해 주세요.", "display_order": 0},
        headers={"Idempotency-Key": f"recommendation-question-{suffix}"},
    )
    assert question.status_code == 201
    return {"project_id": project.json()["id"], "question_id": question.json()["id"]}


def create_synthetic_run(client: TestClient, *, question_id: str, key: str, candidate_limit: int):
    return client.post(
        f"/api/v1/questions/{question_id}/recommendation-runs",
        json={
            "question_version": 1,
            "snapshot_version": 0,
            "candidate_limit": candidate_limit,
            "include_excluded": False,
            "allow_limited_analysis": True,
        },
        headers={"Idempotency-Key": key},
    )


def execute_synthetic_run(
    factory: sessionmaker,
    *,
    owner_user_id: UUID,
    run_id: str,
) -> None:
    with factory.begin() as session:
        SyntheticRecommendationAdapter(session).execute(
            owner_user_id=owner_user_id,
            run_id=UUID(run_id),
        )
