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
def workspace_api_client(
    api_app: FastAPI,
    api_migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    owner_one_id: UUID,
    owner_two_id: UUID,
) -> Iterator[tuple[TestClient, FastAPI, dict[str, UUID]]]:
    test_session_factory = sessionmaker(bind=api_migrated_engine, expire_on_commit=False)
    monkeypatch.setattr(dependencies, "SessionLocal", test_session_factory)
    company_id = uuid4()
    pending_company_id = uuid4()
    with api_migrated_engine.begin() as connection:
        for owner_id, display_name in (
            (owner_one_id, "Workspace API owner one"),
            (owner_two_id, "Workspace API owner two"),
        ):
            connection.execute(
                text(
                    "INSERT INTO users (id, display_name, locale, timezone) "
                    "VALUES (:id, :display_name, 'ko-KR', 'Asia/Seoul')"
                ),
                {"id": owner_id, "display_name": display_name},
            )
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, legal_name, display_name, official_domain, identification_status) "
                "VALUES (:id, 'EPICK 주식회사', 'EPICK', 'epick.example', 'VERIFIED')"
            ),
            {"id": company_id},
        )
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, legal_name, display_name, identification_status) "
                "VALUES (:id, '미확정 회사', '미확정 회사', 'PENDING')"
            ),
            {"id": pending_company_id},
        )
    with TestClient(api_app) as client:
        yield (
            client,
            api_app,
            {
                "one": owner_one_id,
                "two": owner_two_id,
                "company": company_id,
                "pending_company": pending_company_id,
            },
        )


def _project_payload(company_id: UUID, *, title: str = "EPICK 백엔드 지원") -> dict[str, object]:
    return {
        "title": title,
        "company_id": str(company_id),
        "season": "2026 하반기",
        "role_name": "Backend Engineer",
    }


@pytest.mark.postgres
def test_company_catalog_project_versions_and_public_status_mapping(
    workspace_api_client: tuple[TestClient, FastAPI, dict[str, UUID]],
    api_migrated_engine: Engine,
) -> None:
    client, app, ids = workspace_api_client
    override_principal(app, ids["one"], subject="workspace-owner-one")

    me = client.get("/api/v1/users/me")
    home = client.get("/api/v1/home")
    catalog = client.get("/api/v1/companies", params={"query": "EPICK"})
    unknown_catalog = client.get("/api/v1/companies", params={"query": "없는 회사"})
    rejected_create = client.post(
        "/api/v1/application-projects",
        json=_project_payload(ids["pending_company"]),
        headers={"Idempotency-Key": "pending-company"},
    )

    assert me.status_code == 200
    assert me.json()["id"] == str(ids["one"])
    assert home.status_code == 200
    assert home.json()["experience_store"] == {"activity_count": 0, "draft_count": 0}
    assert catalog.status_code == 200
    assert [item["id"] for item in catalog.json()["items"]] == [str(ids["company"])]
    assert unknown_catalog.json() == {"items": [], "next_cursor": None}
    assert rejected_create.status_code == 422
    assert rejected_create.json()["error"]["fields"] == [
        {"field": "company_id", "reason": "NOT_A_RESOLVED_CATALOG_ENTRY"}
    ]

    first = client.post(
        "/api/v1/application-projects",
        json=_project_payload(ids["company"]),
        headers={"Idempotency-Key": "project-create"},
    )
    replayed = client.post(
        "/api/v1/application-projects",
        json=_project_payload(ids["company"]),
        headers={"Idempotency-Key": "project-create"},
    )
    second = client.post(
        "/api/v1/application-projects",
        json=_project_payload(ids["company"], title="EPICK 재지원"),
        headers={"Idempotency-Key": "project-create-second"},
    )
    project_id = first.json()["id"]

    assert first.status_code == 201
    assert replayed.status_code == 201
    assert replayed.json() == first.json()
    assert first.headers["Location"] == f"/api/v1/application-projects/{project_id}"
    assert first.headers["ETag"] == '"1"'
    assert second.status_code == 201
    assert second.json()["id"] != project_id

    updated = client.patch(
        f"/api/v1/application-projects/{project_id}",
        json={"title": "EPICK 플랫폼 백엔드 지원", "change_reason": "지원 제목 보정"},
        headers={"If-Match": '"1"', "Idempotency-Key": "project-update"},
    )
    stale = client.patch(
        f"/api/v1/application-projects/{project_id}",
        json={"title": "오래된 제목", "change_reason": "오래된 수정"},
        headers={"If-Match": '"1"', "Idempotency-Key": "project-update-stale"},
    )
    versions = client.get(f"/api/v1/application-projects/{project_id}/versions")

    assert updated.status_code == 200
    assert updated.headers["ETag"] == '"2"'
    assert updated.json()["changed_fields"] == ["title"]
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert [item["version"] for item in versions.json()] == [1, 2]

    with api_migrated_engine.begin() as connection:
        connection.execute(
            text("UPDATE application_projects SET status = 'STALE' WHERE id = :id"),
            {"id": UUID(project_id)},
        )
    mapped = client.get(f"/api/v1/application-projects/{project_id}")
    assert mapped.status_code == 200
    assert mapped.json()["status"] == "NEEDS_REVIEW"

    with api_migrated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs "
                "(id, owner_user_id, project_id, job_type, owner_deletion_epoch, status) "
                "VALUES (:id, :owner_user_id, :project_id, 'QUESTION_ANALYSIS', 0, 'WAITING_USER')"
            ),
            {
                "id": uuid4(),
                "owner_user_id": ids["one"],
                "project_id": UUID(project_id),
            },
        )
    paused = client.get(f"/api/v1/application-projects/{project_id}")
    assert paused.status_code == 200
    assert paused.json()["status"] == "PAUSED"

    override_principal(app, ids["two"], subject="workspace-owner-two")
    hidden = client.get(f"/api/v1/application-projects/{project_id}")
    assert hidden.status_code == 404


@pytest.mark.postgres
def test_questions_append_versions_preserve_order_and_archive_without_deleting_history(
    workspace_api_client: tuple[TestClient, FastAPI, dict[str, UUID]],
) -> None:
    client, app, ids = workspace_api_client
    override_principal(app, ids["one"])
    project = client.post(
        "/api/v1/application-projects",
        json=_project_payload(ids["company"]),
        headers={"Idempotency-Key": "question-project"},
    )
    project_id = project.json()["id"]

    first = client.post(
        f"/api/v1/application-projects/{project_id}/questions",
        json={
            "prompt": "협업 경험을 설명해 주세요.",
            "character_limit": 800,
            "display_order": 0,
            "source": "USER_INPUT",
        },
        headers={"Idempotency-Key": "question-create-1"},
    )
    second = client.post(
        f"/api/v1/application-projects/{project_id}/questions",
        json={
            "prompt": "문제를 해결한 경험을 설명해 주세요.",
            "display_order": 1,
            "source": "USER_INPUT",
        },
        headers={"Idempotency-Key": "question-create-2"},
    )
    duplicate_order = client.post(
        f"/api/v1/application-projects/{project_id}/questions",
        json={
            "prompt": "중복 정렬 문항",
            "display_order": 1,
            "source": "USER_INPUT",
        },
        headers={"Idempotency-Key": "question-create-duplicate"},
    )
    question_id = first.json()["id"]

    assert first.status_code == 201
    assert second.status_code == 201
    assert duplicate_order.status_code == 422
    assert duplicate_order.json()["error"]["fields"] == [
        {"field": "display_order", "reason": "ALREADY_IN_USE"}
    ]

    listed = client.get(f"/api/v1/application-projects/{project_id}/questions")
    updated = client.patch(
        f"/api/v1/questions/{question_id}",
        json={"prompt": "협업 과정에서 맡은 역할을 설명해 주세요."},
        headers={"If-Match": '"1"', "Idempotency-Key": "question-update"},
    )
    stale = client.patch(
        f"/api/v1/questions/{question_id}",
        json={"prompt": "오래된 문항"},
        headers={"If-Match": '"1"', "Idempotency-Key": "question-update-stale"},
    )
    versions = client.get(f"/api/v1/questions/{question_id}/versions")
    archived = client.delete(
        f"/api/v1/questions/{question_id}",
        headers={"If-Match": '"2"', "Idempotency-Key": "question-delete"},
    )
    replayed_archive = client.delete(
        f"/api/v1/questions/{question_id}",
        headers={"If-Match": '"2"', "Idempotency-Key": "question-delete"},
    )
    after_archive = client.get(f"/api/v1/application-projects/{project_id}/questions")
    archived_detail = client.get(f"/api/v1/questions/{question_id}")

    assert [item["display_order"] for item in listed.json()] == [0, 1]
    assert updated.status_code == 200
    assert updated.json()["current_version"] == 2
    assert stale.status_code == 412
    assert [item["version"] for item in versions.json()] == [1, 2]
    assert archived.status_code == 204
    assert replayed_archive.status_code == 204
    assert [item["id"] for item in after_archive.json()] == [second.json()["id"]]
    assert archived_detail.json()["status"] == "ARCHIVED"

    override_principal(app, ids["two"])
    hidden = client.get(f"/api/v1/questions/{question_id}")
    assert hidden.status_code == 404


@pytest.mark.postgres
def test_official_url_job_posting_request_remains_egress_gated(
    workspace_api_client: tuple[TestClient, FastAPI, dict[str, UUID]],
    api_migrated_engine: Engine,
) -> None:
    client, app, ids = workspace_api_client
    override_principal(app, ids["one"])
    project = client.post(
        "/api/v1/application-projects",
        json=_project_payload(ids["company"]),
        headers={"Idempotency-Key": "egress-gate-project"},
    )

    blocked = client.post(
        f"/api/v1/application-projects/{project.json()['id']}/job-posting",
        json={"mode": "OFFICIAL_URL", "official_url": "https://careers.example/jobs/1"},
        headers={"If-Match": '"1"', "Idempotency-Key": "egress-gate"},
    )

    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "EXECUTION_POLICY_UNCONFIGURED"
    with api_migrated_engine.connect() as connection:
        job_count = connection.scalar(
            text("SELECT count(*) FROM jobs WHERE project_id = :project_id"),
            {"project_id": UUID(project.json()["id"])},
        )
    assert job_count == 0
