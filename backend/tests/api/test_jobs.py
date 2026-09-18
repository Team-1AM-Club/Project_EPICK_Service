from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db.session import set_local_owner_context
from app.models.application_workspace import Company
from app.models.jobs import Job, JobRequiredAction
from app.models.sources import JobSourceLink, Source
from app.services.core_decision_inbound import CoreDecisionInboundService
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


@pytest.mark.postgres
def test_non_core_decision_public_job_exposes_stop_only(
    job_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = job_api_client
    with factory.begin() as session:
        set_local_owner_context(session, ids["one"])
        company = Company(legal_name="Non-core API", display_name="Non-core API")
        session.add(company)
        session.flush()
        source = Source(
            company_id=company.id,
            source_type="CAREERS",
            canonical_url=f"https://example.test/non-core/{uuid4()}",
            canonical_url_hash=f"non-core-{uuid4()}",
            url_normalization_version="v1",
            policy_version="policy-v1",
            policy_checked_at=datetime.now(UTC),
        )
        job = Job(
            owner_user_id=ids["one"],
            job_type="SOURCE_COLLECTION",
            status="WAITING_USER",
            dispatch_status="BLOCKED",
            owner_deletion_epoch=0,
            analysis_input_version="analysis-v1",
        )
        session.add_all([source, job])
        session.flush()
        session.add_all(
            [
                JobSourceLink(
                    job_id=job.id,
                    owner_user_id=ids["one"],
                    source_id=source.id,
                    source_version_id=None,
                    command_id=None,
                    purpose_ref="SOURCE_COLLECTION",
                    analysis_input_version="analysis-v1",
                ),
                JobRequiredAction(
                    job_id=job.id,
                    owner_user_id=ids["one"],
                    action_code="CORE_DECISION_REQUIRED",
                    action_status="OPEN",
                    expected_input_version="analysis-v1",
                ),
            ]
        )
        session.flush()
        CoreDecisionInboundService(session).apply(
            body={
                "schema_version": "w3.private.core-decision/0.1-candidate",
                "message_type": "w3.private.w1.core-decision",
                "message_id": str(uuid4()),
                "occurred_at": "2026-09-18T00:00:00Z",
                "visibility_scope": "PRIVATE",
                "producer": "w3",
                "job_id": str(job.id),
                "company_id": str(company.id),
                "source_id": str(source.id),
                "analysis_input_version": "analysis-v1",
                "decision_scope": "COMPANY_KNOWLEDGE",
                "decision_owner": "W3",
                "question_version_id": None,
                "decision_version": 1,
                "is_core": False,
                "decision_code": "NON_CORE_OPTIONAL",
                "reason_code": "OPTIONAL_COMPANY_EVIDENCE",
            },
            authenticated_principal="w3",
        )
        job_id = job.id

    override_principal(app, ids["one"])
    response = client.get(f"/api/v1/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "WAITING_USER"
    assert [action["code"] for action in response.json()["required_actions"]] == ["STOP"]
