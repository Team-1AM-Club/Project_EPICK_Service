from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.api import dependencies
from tests.api.conftest import override_principal


@pytest.fixture
def experience_api_client(
    api_app: FastAPI,
    api_migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    owner_one_id: UUID,
    owner_two_id: UUID,
) -> Iterator[tuple[TestClient, FastAPI, dict[str, UUID]]]:
    """Run API routes through the real RLS dependency against Alembic head."""

    test_session_factory = sessionmaker(bind=api_migrated_engine, expire_on_commit=False)
    monkeypatch.setattr(dependencies, "SessionLocal", test_session_factory)
    with api_migrated_engine.begin() as connection:
        for owner_id, display_name in (
            (owner_one_id, "Experience API owner one"),
            (owner_two_id, "Experience API owner two"),
        ):
            connection.execute(
                text(
                    "INSERT INTO users (id, display_name, locale, timezone) "
                    "VALUES (:id, :display_name, 'ko-KR', 'Asia/Seoul')"
                ),
                {"id": owner_id, "display_name": display_name},
            )
    with TestClient(api_app) as client:
        yield client, api_app, {"one": owner_one_id, "two": owner_two_id}


def _provided(value: str) -> dict[str, str]:
    return {"value": value, "availability": "PROVIDED"}


def _activity_payload(*, title: str = "EPICK API 구현") -> dict[str, object]:
    return {
        "title": title,
        "organization": _provided("EPICK"),
        "activity_type": "PROJECT",
        "period": {
            "start_date": "2026-09-01",
            "end_date": "2026-09-16",
            "precision": "DAY",
            "availability": "PROVIDED",
        },
        "role": _provided("백엔드 개발"),
        "outcome": {
            "status": "IN_PROGRESS",
            "summary": {"value": None, "availability": "NOT_PROVIDED"},
        },
        "usage_enabled": True,
    }


@pytest.mark.postgres
def test_activity_create_replays_by_idempotency_key_and_hides_other_owner(
    experience_api_client: tuple[TestClient, FastAPI, dict[str, UUID]],
) -> None:
    client, app, owners = experience_api_client
    override_principal(app, owners["one"], subject="owner-one")
    payload = _activity_payload()

    first = client.post(
        "/api/v1/activities", json=payload, headers={"Idempotency-Key": "activity-1"}
    )
    second = client.post(
        "/api/v1/activities", json=payload, headers={"Idempotency-Key": "activity-1"}
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.headers["Location"] == f"/api/v1/activities/{first.json()['id']}"
    assert first.headers["ETag"] == '"1"'
    assert first.json()["registration_status"] == "DRAFT"

    activity_id = first.json()["id"]
    listed = client.get("/api/v1/activities")
    versions = client.get(f"/api/v1/activities/{activity_id}/versions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [activity_id]
    assert listed.json()["next_cursor"] is None
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [1]

    override_principal(app, owners["two"], subject="owner-two")
    hidden = client.get(f"/api/v1/activities/{activity_id}")
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.postgres
def test_activity_update_appends_version_and_rejects_stale_if_match(
    experience_api_client: tuple[TestClient, FastAPI, dict[str, UUID]],
) -> None:
    client, app, owners = experience_api_client
    override_principal(app, owners["one"])
    created = client.post(
        "/api/v1/activities",
        json=_activity_payload(title="초기 제목"),
        headers={"Idempotency-Key": "activity-update-create"},
    )
    activity_id = created.json()["id"]

    updated = client.patch(
        f"/api/v1/activities/{activity_id}",
        json={"title": "수정 제목", "change_reason": "표현 수정"},
        headers={"If-Match": '"1"', "Idempotency-Key": "activity-update-1"},
    )
    stale = client.patch(
        f"/api/v1/activities/{activity_id}",
        json={"title": "오래된 수정", "change_reason": "오래된 요청"},
        headers={"If-Match": '"1"', "Idempotency-Key": "activity-update-stale"},
    )
    later_update = client.patch(
        f"/api/v1/activities/{activity_id}",
        json={"title": "더 최신 제목", "change_reason": "후속 수정"},
        headers={"If-Match": '"2"', "Idempotency-Key": "activity-update-2"},
    )
    replayed = client.patch(
        f"/api/v1/activities/{activity_id}",
        json={"title": "수정 제목", "change_reason": "표현 수정"},
        headers={"If-Match": '"1"', "Idempotency-Key": "activity-update-1"},
    )
    version_one = client.get(f"/api/v1/activities/{activity_id}/versions/1")

    assert updated.status_code == 200
    assert updated.json()["current_version"] == 2
    assert updated.json()["changed_fields"] == ["title"]
    assert updated.headers["ETag"] == '"2"'
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["fields"] == [
        {"field": "If-Match", "reason": "EXPECTED_1_ACTUAL_2"}
    ]
    assert later_update.status_code == 200
    assert later_update.json()["current_version"] == 3
    assert replayed.status_code == 200
    assert replayed.json()["title"] == "수정 제목"
    assert replayed.json()["current_version"] == 2
    assert replayed.headers["ETag"] == '"2"'
    assert version_one.json()["title"] == "초기 제목"


@pytest.mark.postgres
def test_activity_completion_and_episode_version_flow(
    experience_api_client: tuple[TestClient, FastAPI, dict[str, UUID]],
) -> None:
    client, app, owners = experience_api_client
    override_principal(app, owners["one"])

    incomplete = client.post(
        "/api/v1/activities",
        json={"title": "미완료 활동"},
        headers={"Idempotency-Key": "incomplete-activity"},
    )
    incomplete_id = incomplete.json()["id"]
    completion_failure = client.post(
        f"/api/v1/activities/{incomplete_id}/complete",
        json={},
        headers={"If-Match": '"1"', "Idempotency-Key": "incomplete-complete"},
    )
    assert completion_failure.status_code == 422
    assert completion_failure.json()["error"]["code"] == "COMPLETION_REQUIREMENTS_NOT_MET"
    assert {field["field"] for field in completion_failure.json()["error"]["fields"]} >= {
        "organization",
        "activity_type",
        "period",
        "role",
        "outcome.status",
    }

    activity = client.post(
        "/api/v1/activities",
        json=_activity_payload(title="Episode 상위 활동"),
        headers={"Idempotency-Key": "episode-activity"},
    )
    activity_id = activity.json()["id"]
    activity_completed = client.post(
        f"/api/v1/activities/{activity_id}/complete",
        json={},
        headers={"If-Match": '"1"', "Idempotency-Key": "episode-activity-complete"},
    )
    episode_payload = {
        "title": "실제 API 연결",
        "situation": _provided("DB와 FastAPI를 연결함"),
        "actions": _provided("RLS 테스트를 작성함"),
        "technologies": ["FastAPI", "PostgreSQL"],
    }
    episode = client.post(
        f"/api/v1/activities/{activity_id}/episodes",
        json=episode_payload,
        headers={"Idempotency-Key": "episode-create"},
    )
    episode_id = episode.json()["id"]
    episode_list = client.get(f"/api/v1/activities/{activity_id}/episodes")
    changed = client.patch(
        f"/api/v1/episodes/{episode_id}",
        json={
            "actions": _provided("서비스와 라우터를 연결하고 검증함"),
            "technologies": ["FastAPI", "PostgreSQL", "Pytest"],
            "change_reason": "구현 내역 보강",
        },
        headers={"If-Match": '"1"', "Idempotency-Key": "episode-update"},
    )
    old_version = client.get(f"/api/v1/episodes/{episode_id}/versions/1")
    completed = client.post(
        f"/api/v1/episodes/{episode_id}/complete",
        json={},
        headers={"If-Match": '"2"', "Idempotency-Key": "episode-complete"},
    )

    assert episode.status_code == 201
    assert activity_completed.status_code == 200
    assert activity_completed.json()["registration_status"] == "COMPLETED"
    assert activity_completed.json()["current_version"] == 2
    assert [item["id"] for item in episode_list.json()] == [episode_id]
    assert episode.json()["technologies"] == ["FastAPI", "PostgreSQL"]
    assert changed.status_code == 200
    assert changed.json()["current_version"] == 2
    assert changed.json()["technologies"] == ["FastAPI", "PostgreSQL", "Pytest"]
    assert old_version.json()["actions"]["value"] == "RLS 테스트를 작성함"
    assert old_version.json()["technologies"] == ["FastAPI", "PostgreSQL"]
    assert completed.status_code == 200
    assert completed.json()["registration_status"] == "COMPLETED"
    assert completed.json()["current_version"] == 3
