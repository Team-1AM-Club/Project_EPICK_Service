from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.api import dependencies
from tests.api.conftest import override_principal


@pytest.fixture
def frontend_workspace_client(
    api_app: FastAPI,
    api_migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    owner_one_id: UUID,
    owner_two_id: UUID,
) -> Iterator[tuple[TestClient, FastAPI, dict[str, UUID]]]:
    monkeypatch.setattr(
        dependencies,
        "SessionLocal",
        sessionmaker(bind=api_migrated_engine, expire_on_commit=False),
    )
    company_id = uuid4()
    with api_migrated_engine.begin() as connection:
        for owner_id, name in ((owner_one_id, "Frontend one"), (owner_two_id, "Frontend two")):
            connection.execute(
                text(
                    "INSERT INTO users (id, display_name, locale, timezone) "
                    "VALUES (:id, :name, 'ko-KR', 'Asia/Seoul')"
                ),
                {"id": owner_id, "name": name},
            )
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, legal_name, display_name, official_domain, identification_status) "
                "VALUES (:id, 'EPICK 주식회사', 'EPICK', 'epick.test', 'VERIFIED')"
            ),
            {"id": company_id},
        )
    with TestClient(api_app) as client:
        yield client, api_app, {"one": owner_one_id, "two": owner_two_id, "company": company_id}


@pytest.mark.postgres
def test_frontend_crud_contract_is_replayable_paginated_and_owner_hidden(
    frontend_workspace_client: tuple[TestClient, FastAPI, dict[str, UUID]],
) -> None:
    client, app, ids = frontend_workspace_client
    override_principal(app, ids["one"], subject="frontend-owner-one")

    activity_payload = {
        "title": "프런트 연동 경험",
        "organization": {"value": None, "availability": "NOT_PROVIDED"},
        "activity_type": "PROJECT",
        "period": {
            "start_date": None,
            "end_date": None,
            "precision": None,
            "availability": "NOT_PROVIDED",
        },
        "role": {"value": "개발", "availability": "PROVIDED"},
        "outcome": {
            "status": "IN_PROGRESS",
            "summary": {"value": None, "availability": "NOT_PROVIDED"},
        },
        "usage_enabled": True,
    }
    activity = client.post(
        "/api/v1/activities",
        json=activity_payload,
        headers={"Idempotency-Key": "frontend-activity"},
    )
    replayed_activity = client.post(
        "/api/v1/activities",
        json=activity_payload,
        headers={"Idempotency-Key": "frontend-activity"},
    )
    activity_id = activity.json()["id"]
    episode = client.post(
        f"/api/v1/activities/{activity_id}/episodes",
        json={"title": "초기 사건", "original_narrative": "브라우저 입력"},
        headers={"Idempotency-Key": "frontend-episode"},
    )
    activity_page = client.get("/api/v1/activities", params={"limit": 1})

    assert activity.status_code == 201
    assert replayed_activity.json()["id"] == activity_id
    assert episode.status_code == 201
    assert activity_page.status_code == 200
    assert len(activity_page.json()["items"]) == 1

    project_payload = {
        "title": "EPICK 지원",
        "company_id": str(ids["company"]),
        "role_name": "Backend Engineer",
    }
    project = client.post(
        "/api/v1/application-projects",
        json=project_payload,
        headers={"Idempotency-Key": "frontend-project"},
    )
    project_id = project.json()["id"]
    question = client.post(
        f"/api/v1/application-projects/{project_id}/questions",
        json={"prompt": "협업 경험", "display_order": 0, "source": "USER_INPUT"},
        headers={"Idempotency-Key": "frontend-question"},
    )
    updated = client.patch(
        f"/api/v1/application-projects/{project_id}",
        json={"role_name": "Platform Engineer", "change_reason": "사용자 수정"},
        headers={"If-Match": '"1"', "Idempotency-Key": "frontend-project-update"},
    )
    stale = client.patch(
        f"/api/v1/application-projects/{project_id}",
        json={"role_name": "Stale", "change_reason": "오래된 화면"},
        headers={"If-Match": '"1"', "Idempotency-Key": "frontend-project-stale"},
    )

    assert project.status_code == 201
    assert question.status_code == 201
    assert updated.status_code == 200
    assert updated.headers["ETag"] == '"2"'
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    override_principal(app, ids["two"], subject="frontend-owner-two")
    assert client.get(f"/api/v1/activities/{activity_id}").status_code == 404
    assert client.get(f"/api/v1/application-projects/{project_id}").status_code == 404
    assert client.get(f"/api/v1/questions/{question.json()['id']}").status_code == 404
