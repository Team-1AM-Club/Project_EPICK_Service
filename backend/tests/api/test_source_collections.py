from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from tests.api.conftest import override_principal


@pytest.fixture
def source_collection_api_client(
    api_app: FastAPI,
    api_migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker]:
    from app.api import dependencies

    factory = sessionmaker(bind=api_migrated_engine, expire_on_commit=False)
    monkeypatch.setattr(dependencies, "SessionLocal", factory)
    ids = {
        "owner": uuid4(),
        "other_owner": uuid4(),
        "company": uuid4(),
        "project": uuid4(),
        "project_version": uuid4(),
    }
    with api_migrated_engine.begin() as connection:
        for owner in (ids["owner"], ids["other_owner"]):
            connection.execute(
                text(
                    "INSERT INTO users (id, display_name, locale, timezone) "
                    "VALUES (:id, 'Source collection owner', 'ko-KR', 'Asia/Seoul')"
                ),
                {"id": owner},
            )
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, legal_name, display_name, official_domain, identification_status) "
                "VALUES (:id, 'Example Corp', 'Example', 'careers.example.com', 'VERIFIED')"
            ),
            {"id": ids["company"]},
        )
        connection.execute(
            text(
                "INSERT INTO application_projects "
                "(id, owner_user_id, current_version_id, status, lock_version) "
                "VALUES (:id, :owner, NULL, 'DRAFT', 1)"
            ),
            {"id": ids["project"], "owner": ids["owner"]},
        )
        connection.execute(
            text(
                "INSERT INTO application_project_versions "
                "(id, project_id, owner_user_id, version_no, company_id, title, role_name) "
                "VALUES (:id, :project, :owner, 1, :company, 'Example application', 'Engineer')"
            ),
            {
                "id": ids["project_version"],
                "project": ids["project"],
                "owner": ids["owner"],
                "company": ids["company"],
            },
        )
        connection.execute(
            text("UPDATE application_projects SET current_version_id=:version WHERE id=:project"),
            {"version": ids["project_version"], "project": ids["project"]},
        )
    return TestClient(api_app), api_app, ids, factory


@pytest.mark.postgres
def test_accepts_official_url_and_replays_same_job(
    source_collection_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, _factory = source_collection_api_client
    override_principal(app, ids["owner"])
    path = f"/api/v1/application-projects/{ids['project']}/source-collections"
    body = {
        "source_type": "OFFICIAL_URL",
        "official_url": "HTTPS://CAREERS.EXAMPLE.COM:443/jobs/42",
        "purpose": "JOB_POSTING",
    }

    first = client.post(path, headers={"Idempotency-Key": "collect-42"}, json=body)
    second = client.post(path, headers={"Idempotency-Key": "collect-42"}, json=body)

    assert first.status_code == 202
    assert first.headers["location"] == f"/api/v1/jobs/{first.json()['job_id']}"
    assert first.json()["status"] == "QUEUED"
    assert first.json()["replayed"] is False
    assert second.status_code == 202
    assert second.json() == {**first.json(), "replayed": True}
    progress = client.get(
        f"{path}/{first.json()['job_id']}"
    )
    assert progress.status_code == 200
    assert progress.json() == {
        "job_id": first.json()["job_id"],
        "source_id": first.json()["source_id"],
        "status": "QUEUED",
        "stage": None,
        "progress": {"completed_units": 0, "total_units": None, "percent": None},
    }
    assert client.get(path).json() == progress.json()

    override_principal(app, ids["other_owner"])
    assert client.get(f"{path}/{first.json()['job_id']}").status_code == 404


def test_openapi_exposes_required_idempotency_and_acceptance_schema(api_app: FastAPI) -> None:
    operation = api_app.openapi()["paths"][
        "/api/v1/application-projects/{project_id}/source-collections"
    ]["post"]
    idempotency = next(
        parameter for parameter in operation["parameters"] if parameter["name"] == "Idempotency-Key"
    )
    assert idempotency["required"] is True
    assert operation["responses"]["202"]["headers"]["Location"]
    assert operation["responses"]["202"]["content"]["application/json"]["schema"]


@pytest.mark.postgres
def test_source_collection_is_owner_scoped(
    source_collection_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, _factory = source_collection_api_client
    override_principal(app, ids["other_owner"])
    response = client.post(
        f"/api/v1/application-projects/{ids['project']}/source-collections",
        headers={"Idempotency-Key": "other-owner"},
        json={
            "source_type": "OFFICIAL_URL",
            "official_url": "https://careers.example.com/jobs/42",
            "purpose": "JOB_POSTING",
        },
    )
    assert response.status_code == 404


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("official_url", "status_code", "reason"),
    [
        ("ftp://careers.example.com/jobs/42", 400, "UNSUPPORTED_URL"),
        ("https://user:secret@careers.example.com/jobs/42", 400, "UNSUPPORTED_URL"),
        ("https://careers.example.com/jobs/42#details", 400, "UNSUPPORTED_URL"),
        ("https://evil.example.net/jobs/42", 422, "COMPANY_DOMAIN_MISMATCH"),
    ],
)
def test_rejects_unsafe_or_unowned_urls(
    source_collection_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
    official_url: str,
    status_code: int,
    reason: str,
) -> None:
    client, app, ids, _factory = source_collection_api_client
    override_principal(app, ids["owner"])
    response = client.post(
        f"/api/v1/application-projects/{ids['project']}/source-collections",
        headers={"Idempotency-Key": f"invalid-{reason}"},
        json={
            "source_type": "OFFICIAL_URL",
            "official_url": official_url,
            "purpose": "JOB_POSTING",
        },
    )
    assert response.status_code == status_code
    assert response.json()["error"]["fields"] == [
        {"field": "official_url", "reason": reason}
    ]


@pytest.mark.postgres
def test_same_key_with_different_request_conflicts(
    source_collection_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, _factory = source_collection_api_client
    override_principal(app, ids["owner"])
    path = f"/api/v1/application-projects/{ids['project']}/source-collections"
    headers = {"Idempotency-Key": "conflicting-source"}
    base = {"source_type": "OFFICIAL_URL", "purpose": "JOB_POSTING"}
    assert client.post(
        path,
        headers=headers,
        json={**base, "official_url": "https://careers.example.com/jobs/42"},
    ).status_code == 202
    conflict = client.post(
        path,
        headers=headers,
        json={**base, "official_url": "https://careers.example.com/jobs/43"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
