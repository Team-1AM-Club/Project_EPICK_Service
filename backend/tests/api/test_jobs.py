from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.api.conftest import override_principal
from tests.api.job_support import accept_job


@pytest.mark.postgres
def test_jobs_are_owner_scoped_and_acceptance_does_not_claim_dispatch(
    job_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = job_api_client
    job_id = accept_job(factory, owner_user_id=ids["one"], key="job-list-owner")
    override_principal(app, ids["one"])

    detail = client.get(f"/api/v1/jobs/{job_id}")
    listed = client.get("/api/v1/jobs")

    assert detail.status_code == 200
    assert detail.json()["status"] == "QUEUED"
    assert detail.json()["dispatch_status"] == "OUTBOX_PENDING"
    assert "command_id" not in detail.json()
    assert "execution_fence" not in detail.json()
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [job_id]

    override_principal(app, ids["two"])
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert client.get("/api/v1/jobs").json()["items"] == []
