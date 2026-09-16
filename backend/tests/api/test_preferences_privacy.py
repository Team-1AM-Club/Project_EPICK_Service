from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from tests.api.conftest import override_principal
from tests.api.recommendation_support import create_recommendation_workspace, create_synthetic_run


@pytest.mark.postgres
def test_settings_preferences_exclusions_and_consent_are_owner_scoped(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, _ = recommendation_api_client
    override_principal(app, ids["one"])

    settings = client.get("/api/v1/settings")
    assert settings.status_code == 200
    changed_settings = client.patch(
        "/api/v1/settings",
        json={"timezone": "UTC", "display_options": {"compact": True}},
        headers={"If-Match": settings.headers["ETag"], "Idempotency-Key": "api5-settings"},
    )
    assert changed_settings.status_code == 200
    assert changed_settings.json()["version"] == 2
    stale = client.patch(
        "/api/v1/settings",
        json={"locale": "en-US"},
        headers={"If-Match": settings.headers["ETag"], "Idempotency-Key": "api5-settings-stale"},
    )
    assert stale.status_code == 412

    preferences = client.get("/api/v1/recommendation-preferences")
    changed_preferences = client.patch(
        "/api/v1/recommendation-preferences",
        json={"default_candidate_limit": 7, "show_information_completeness": True},
        headers={"If-Match": preferences.headers["ETag"], "Idempotency-Key": "api5-preferences"},
    )
    assert changed_preferences.status_code == 200
    assert changed_preferences.json()["default_candidate_limit"] == 7

    activity = client.post(
        "/api/v1/activities",
        json={"title": "API-5 exclusion activity"},
        headers={"Idempotency-Key": "api5-exclusion-activity"},
    )
    resume_items = client.get("/api/v1/resume-items", params={"type": "ACTIVITY_DRAFT"})
    assert resume_items.status_code == 200
    assert resume_items.json()["items"][0]["resource_id"] == activity.json()["id"]
    assert resume_items.json()["items"][0]["resume_url"] == f"/activities/{activity.json()['id']}"
    assert client.get("/api/v1/home").json()["resume_items"]
    exclusion = client.post(
        "/api/v1/experience-exclusions",
        json={"activity_id": activity.json()["id"], "scope": "GLOBAL"},
        headers={"Idempotency-Key": "api5-exclusion"},
    )
    assert exclusion.status_code == 201
    listed_exclusions = client.get("/api/v1/experience-exclusions").json()["items"]
    assert listed_exclusions[0]["id"] == exclusion.json()["id"]
    assert client.delete(
        f"/api/v1/experience-exclusions/{exclusion.json()['id']}",
        headers={"Idempotency-Key": "api5-exclusion-revoke"},
    ).status_code == 204

    assert client.get("/api/v1/consents").json() == [
        {"type": "ANALYTICS", "opted_in": False, "policy_version": None, "decided_at": None}
    ]
    consent = client.patch(
        "/api/v1/consents/analytics",
        json={"opted_in": True, "policy_version": "privacy-2026-09"},
        headers={"Idempotency-Key": "api5-analytics-consent"},
    )
    assert consent.status_code == 200
    assert consent.json()["opted_in"] is True
    assert client.get("/api/v1/data-retention").json() == {"status": "PENDING_CONFIGURATION"}
    assert client.patch("/api/v1/data-retention", json={"option_id": "NEVER"}).status_code == 503

    override_principal(app, ids["two"])
    assert client.get("/api/v1/experience-exclusions").json()["items"] == []
    assert client.get("/api/v1/consents").json()[0]["opted_in"] is False


@pytest.mark.postgres
def test_feedback_never_echoes_free_text_or_creates_analytics_event(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = recommendation_api_client
    override_principal(app, ids["one"])
    workspace = create_recommendation_workspace(
        client, company_id=ids["company"], suffix="feedback"
    )
    run = create_synthetic_run(
        client, question_id=workspace["question_id"], key="api5-feedback-run", candidate_limit=1
    )
    assert run.status_code == 202

    result = client.post(
        f"/api/v1/recommendation-runs/{run.json()['id']}/feedback",
        json={
            "category_l1": "RESULT_QUALITY",
            "decision_helpfulness": "NOT_HELPFUL",
            "other_text": "이 텍스트는 응답이나 분석 이벤트로 되돌아오면 안 됩니다.",
        },
        headers={"Idempotency-Key": "api5-run-feedback"},
    )
    assert result.status_code == 201
    assert "other_text" not in result.json()
    with factory.begin() as session:
        assert session.scalar(text("SELECT count(*) FROM analytics_events")) == 0

    generic = client.post(
        "/api/v1/feedback",
        json={"category_l1": "OTHER", "other_text": "일반 피드백"},
        headers={"Idempotency-Key": "api5-general-feedback"},
    )
    assert generic.status_code == 201
    assert "other_text" not in generic.json()
