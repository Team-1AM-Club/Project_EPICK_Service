from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.api.conftest import override_principal


@pytest.mark.postgres
def test_account_deletion_preview_confirmation_is_idempotent_and_owner_scoped(
    job_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, _ = job_api_client
    override_principal(app, ids["one"])

    preview = client.post("/api/v1/account/deletion-previews")
    assert preview.status_code == 201
    preview_body = preview.json()
    assert set(preview_body) == {
        "deletion_request_id",
        "target_type",
        "scope",
        "preview_token",
        "expires_at",
    }
    assert preview_body["target_type"] == "ACCOUNT"
    assert preview_body["scope"] == "ALL_PRIVATE_DATA"

    confirmation_body = {
        "deletion_request_id": preview_body["deletion_request_id"],
        "preview_token": preview_body["preview_token"],
        "confirmation": "DELETE",
    }
    accepted = client.post(
        "/api/v1/account/deletion-requests",
        json=confirmation_body,
        headers={"Idempotency-Key": "account-deletion-confirm"},
    )
    replayed = client.post(
        "/api/v1/account/deletion-requests",
        json=confirmation_body,
        headers={"Idempotency-Key": "account-deletion-confirm"},
    )
    assert accepted.status_code == 202
    assert replayed.status_code == 202
    assert replayed.json() == accepted.json()
    assert accepted.headers["Location"] == (
        f"/api/v1/account/deletion-requests/{preview_body['deletion_request_id']}"
    )
    assert accepted.json()["status"] == "RUNNING"
    assert accepted.json()["completed_at"] is None
    assert {target["store"] for target in accepted.json()["targets"]} == {
        "POSTGRESQL",
        "NEO4J",
        "VECTOR",
        "CACHE",
        "CHECKPOINT",
    }
    assert {target["status"] for target in accepted.json()["targets"]} == {"QUEUED"}
    assert "owner_deletion_epoch" not in accepted.json()
    assert "preview_token" not in accepted.json()

    status_response = client.get(
        f"/api/v1/account/deletion-requests/{preview_body['deletion_request_id']}"
    )
    assert status_response.status_code == 200
    assert status_response.json() == accepted.json()

    override_principal(app, ids["two"])
    assert (
        client.get(
            f"/api/v1/account/deletion-requests/{preview_body['deletion_request_id']}"
        ).status_code
        == 404
    )
