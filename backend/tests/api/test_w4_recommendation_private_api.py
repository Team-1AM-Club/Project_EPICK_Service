from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.private.w4_recommendations import create_w4_recommendation_private_app


class _Sessions:
    @contextmanager
    def begin(self):
        yield object()


def _client() -> TestClient:
    return TestClient(
        create_w4_recommendation_private_app(
            session_factory=_Sessions(),
            expected_bearer_token="private-secret",
            expected_principal="epick-w4-recommendation-worker",
            lease_seconds=300,
        )
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer private-secret",
        "X-EPICK-Service-Principal": "epick-w4-recommendation-worker",
    }


def test_private_api_requires_workload_credentials_without_disclosing_run() -> None:
    body = {
        "schema_version": "w1.private.w4.recommendation.acquire.v1",
        "owner_user_id": str(uuid4()),
        "run_id": str(uuid4()),
    }
    response = _client().post("/internal/v1/w4/recommendations/acquire", json=body)
    assert response.status_code == 404
    assert response.json()["code"] == "W4_WORKLOAD_UNAUTHENTICATED"
    assert "private-secret" not in response.text


def test_acquire_returns_only_the_strict_private_binding_projection() -> None:
    owner_id = uuid4()
    run_id = uuid4()
    binding = {
        "owner_user_id": str(owner_id),
        "run_id": str(run_id),
        "project_id": str(uuid4()),
        "question_id": str(uuid4()),
        "question_version_id": str(uuid4()),
        "snapshot_id": str(uuid4()),
        "lease_token": str(uuid4()),
        "lease_expires_at": "2026-09-20T00:00:00Z",
        "context_sha256": "a" * 64,
        "request": {},
        "episode_versions": [],
    }
    with patch(
        "app.api.private.w4_recommendations.W4RecommendationRunStoreService.acquire",
        return_value=binding,
    ):
        response = _client().post(
            "/internal/v1/w4/recommendations/acquire",
            headers=_headers(),
            json={
                "schema_version": "w1.private.w4.recommendation.acquire.v1",
                "owner_user_id": str(owner_id),
                "run_id": str(run_id),
            },
        )
    assert response.status_code == 200
    assert response.json()["binding"] == binding
    assert "database_url" not in response.text
    assert "queue_url" not in response.text


def test_publish_rejects_extra_private_fields_before_service_execution() -> None:
    response = _client().post(
        "/internal/v1/w4/recommendations/publish",
        headers=_headers(),
        json={
            "schema_version": "w1.private.w4.recommendation.lease.v1",
            "run_id": str(uuid4()),
            "lease_token": str(uuid4()),
            "publication": {},
            "database_url": "must-not-be-accepted",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_PRIVATE_REQUEST"
