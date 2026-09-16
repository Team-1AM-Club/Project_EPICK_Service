from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.repo.experience import ExperienceRepository
from app.services.lifecycle_operations import LifecycleOperationsService
from tests.api.conftest import override_principal


def _create_episode(client: TestClient, *, suffix: str) -> tuple[str, str]:
    activity = client.post(
        "/api/v1/activities",
        json={"title": f"API-5 lifecycle activity {suffix}"},
        headers={"Idempotency-Key": f"api5-lifecycle-activity-{suffix}"},
    )
    assert activity.status_code == 201
    episode = client.post(
        f"/api/v1/activities/{activity.json()['id']}/episodes",
        json={"title": f"API-5 lifecycle episode {suffix}"},
        headers={"Idempotency-Key": f"api5-lifecycle-episode-{suffix}"},
    )
    assert episode.status_code == 201
    return activity.json()["id"], episode.json()["id"]


@pytest.mark.postgres
def test_inference_read_decision_is_owner_scoped_and_never_starts_unconfigured_engine(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = recommendation_api_client
    override_principal(app, ids["one"])
    _, episode_id = _create_episode(client, suffix="inference")

    with factory.begin() as session:
        episode = ExperienceRepository(session).get_episode(
            episode_id=UUID(episode_id), owner_user_id=ids["one"]
        )
        assert episode is not None and episode.current_version_id is not None
        suggestion = LifecycleOperationsService(session).create_inference_suggestion(
            owner_user_id=ids["one"],
            episode_id=episode.id,
            episode_version_id=episode.current_version_id,
            suggestion_type="ROLE_CLARIFICATION",
            proposed_value={"role": "backend engineer"},
            model_policy_version="test-policy-v1",
        )
        LifecycleOperationsService(session).add_inference_suggestion_source(
            owner_user_id=ids["one"],
            suggestion_id=suggestion.id,
            episode_version_id=episode.current_version_id,
            field_name="actions",
            source_span_start=0,
            source_span_end=10,
        )

    listed = client.get(f"/api/v1/episodes/{episode_id}/inferences")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "PENDING"
    assert listed.json()["items"][0]["sources"] == [
        {
            "field_name": "actions",
            "source_span_start": 0,
            "source_span_end": 10,
            "reference_type": "EPISODE_VERSION",
        }
    ]

    decision_body = {"decision": "MODIFIED", "modified_value": {"role": "platform engineer"}}
    accepted = client.patch(
        f"/api/v1/episodes/{episode_id}/inferences/{suggestion.id}",
        json=decision_body,
        headers={"Idempotency-Key": "api5-inference-decision"},
    )
    replayed = client.patch(
        f"/api/v1/episodes/{episode_id}/inferences/{suggestion.id}",
        json=decision_body,
        headers={"Idempotency-Key": "api5-inference-decision"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "DECIDED"
    assert accepted.json()["latest_decision"]["decision"] == "MODIFIED"
    assert replayed.json() == accepted.json()

    # The only public start route is intentionally closed until worker dispatch exists.
    blocked = client.post(
        f"/api/v1/episodes/{episode_id}/inference-jobs",
        json={"episode_version": 1, "inference_types": ["ROLE_CLARIFICATION"]},
    )
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "EXECUTION_POLICY_UNCONFIGURED"

    override_principal(app, ids["two"])
    assert client.get(f"/api/v1/episodes/{episode_id}/inferences").status_code == 404


@pytest.mark.postgres
def test_duplicate_suggestion_allows_only_non_merge_decisions(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = recommendation_api_client
    override_principal(app, ids["one"])
    activity_id, first_episode_id = _create_episode(client, suffix="duplicate")
    second_episode = client.post(
        f"/api/v1/activities/{activity_id}/episodes",
        json={"title": "API-5 duplicate second episode"},
        headers={"Idempotency-Key": "api5-duplicate-second-episode"},
    )
    assert second_episode.status_code == 201

    with factory.begin() as session:
        repository = ExperienceRepository(session)
        first = repository.get_episode(
            episode_id=UUID(first_episode_id), owner_user_id=ids["one"]
        )
        second = repository.get_episode(
            episode_id=UUID(second_episode.json()["id"]), owner_user_id=ids["one"]
        )
        assert first is not None and first.current_version_id is not None
        assert second is not None and second.current_version_id is not None
        duplicate = LifecycleOperationsService(session).create_duplicate_suggestion(
            owner_user_id=ids["one"],
            first_episode_id=first.id,
            first_episode_version_id=first.current_version_id,
            second_episode_id=second.id,
            second_episode_version_id=second.current_version_id,
            reason="동일한 행동 서술로 보입니다.",
            model_execution_id=None,
        )

    listed = client.get("/api/v1/experiences/duplicates", params={"activity_id": activity_id})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == str(duplicate.id)
    accepted = client.patch(
        f"/api/v1/experiences/duplicates/{duplicate.id}",
        json={"decision": "KEEP_SEPARATE"},
        headers={"Idempotency-Key": "api5-duplicate-decision"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["latest_decision"]["decision"] == "KEEP_SEPARATE"
    assert client.patch(
        f"/api/v1/experiences/duplicates/{duplicate.id}",
        json={"decision": "MERGE"},
        headers={"Idempotency-Key": "api5-duplicate-merge"},
    ).status_code == 422


@pytest.mark.postgres
def test_notifications_map_error_to_critical_and_mutations_are_idempotent(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = recommendation_api_client
    with factory.begin() as session:
        notification = LifecycleOperationsService(session).create_notification(
            owner_user_id=ids["one"],
            notification_type="RETRY_REQUIRED",
            severity="ERROR",
            title="Action required",
            safe_message="A retry decision is required.",
            action_url="/projects/test",
        )
    override_principal(app, ids["one"])

    listed = client.get("/api/v1/notifications", params={"severity": "CRITICAL"})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == str(notification.id)
    assert listed.json()["items"][0]["severity"] == "CRITICAL"

    updated = client.patch(
        f"/api/v1/notifications/{notification.id}",
        json={"read": True, "archived": True},
        headers={"Idempotency-Key": "api5-notification-state"},
    )
    replayed = client.patch(
        f"/api/v1/notifications/{notification.id}",
        json={"read": True, "archived": True},
        headers={"Idempotency-Key": "api5-notification-state"},
    )
    assert updated.status_code == 200
    assert updated.json()["read"] is True
    assert updated.json()["archived"] is True
    assert replayed.json() == updated.json()

    override_principal(app, ids["two"])
    assert client.patch(
        f"/api/v1/notifications/{notification.id}",
        json={"read": True},
        headers={"Idempotency-Key": "api5-hidden-notification"},
    ).status_code == 404
