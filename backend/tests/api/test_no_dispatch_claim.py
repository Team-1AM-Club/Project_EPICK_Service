from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.api.conftest import override_principal
from tests.api.job_support import pause_job_with_action


@pytest.mark.postgres
def test_job_202_means_command_acceptance_not_actual_dispatch(
    job_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = job_api_client
    paused = pause_job_with_action(
        factory,
        owner_user_id=ids["one"],
        key="job-no-dispatch-claim",
        status="PAUSED_RATE_LIMIT",
        action_code="RETRY",
        checkpoint=True,
    )
    override_principal(app, ids["one"])

    accepted = client.post(
        f"/api/v1/jobs/{paused['job_id']}/retry",
        json={
            "required_action_id": paused["action_id"],
            "from_stage": "RETRIEVING_CANDIDATES",
            "expected_input_version": "analysis-v1",
            "expected_result_version": "result-v3",
            "acknowledge_rate_limit": True,
        },
        headers={"Idempotency-Key": "job-no-dispatch-claim-request"},
    )

    assert accepted.status_code == 202
    assert accepted.headers["Location"] == f"/api/v1/jobs/{paused['job_id']}"
    assert accepted.json()["status"] == "QUEUED"
    assert accepted.json()["dispatch_status"] == "OUTBOX_PENDING"
    assert accepted.json()["checkpoint"]["available"] is False
    assert {
        "command_id",
        "dispatch_receipt",
        "execution_fence",
        "lease_id",
        "outbox_id",
        "owner_deletion_epoch",
    }.isdisjoint(accepted.json())
