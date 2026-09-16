from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from tests.api.conftest import override_principal
from tests.api.job_support import accept_job, pause_job_with_action


@pytest.mark.postgres
def test_retry_shortcut_is_fenced_idempotent_and_only_accepts_dispatch(
    job_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = job_api_client
    paused = pause_job_with_action(
        factory,
        owner_user_id=ids["one"],
        key="job-retry",
        status="PAUSED_RATE_LIMIT",
        action_code="RETRY",
        checkpoint=True,
    )
    override_principal(app, ids["one"])
    body = {
        "required_action_id": paused["action_id"],
        "from_stage": "RETRIEVING_CANDIDATES",
        "expected_input_version": "analysis-v1",
        "expected_result_version": "result-v3",
        "acknowledge_rate_limit": True,
    }

    accepted = client.post(
        f"/api/v1/jobs/{paused['job_id']}/retry",
        json=body,
        headers={"Idempotency-Key": "job-retry-request"},
    )
    replayed = client.post(
        f"/api/v1/jobs/{paused['job_id']}/retry",
        json=body,
        headers={"Idempotency-Key": "job-retry-request"},
    )

    assert accepted.status_code == 202
    assert accepted.json()["status"] == "QUEUED"
    assert accepted.json()["dispatch_status"] == "OUTBOX_PENDING"
    assert accepted.json()["checkpoint"]["available"] is False
    assert replayed.status_code == 202
    assert replayed.json() == accepted.json()


@pytest.mark.postgres
def test_action_rejects_stale_fence_without_creating_a_command_or_outbox(
    job_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = job_api_client
    paused = pause_job_with_action(
        factory,
        owner_user_id=ids["one"],
        key="job-stale-action",
        status="WAITING_USER",
        action_code="CONTINUE_LIMITED",
    )
    override_principal(app, ids["one"])
    rejected = client.post(
        f"/api/v1/jobs/{paused['job_id']}/actions",
        json={
            "required_action_id": paused["action_id"],
            "action": "CONTINUE_LIMITED",
            "expected_input_version": "outdated-input",
            "expected_result_version": "result-v3",
        },
        headers={"Idempotency-Key": "job-stale-action-request"},
    )

    with factory.begin() as session:
        command_count = session.scalar(
            text("SELECT count(*) FROM job_commands WHERE job_id = :job_id"),
            {"job_id": paused["job_id"]},
        )
        outbox_count = session.scalar(
            text("SELECT count(*) FROM outbox_messages WHERE job_id = :job_id"),
            {"job_id": paused["job_id"]},
        )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "STALE_INPUT"
    assert command_count == 1
    assert outbox_count == 1


@pytest.mark.postgres
def test_continue_limited_and_cancel_keep_acceptance_separate_from_worker_execution(
    job_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = job_api_client
    paused = pause_job_with_action(
        factory,
        owner_user_id=ids["one"],
        key="job-continue",
        status="WAITING_USER",
        action_code="CONTINUE_LIMITED",
    )
    stopped_job = pause_job_with_action(
        factory,
        owner_user_id=ids["one"],
        key="job-stop",
        status="WAITING_USER",
        action_code="STOP",
    )
    queued_job_id = accept_job(factory, owner_user_id=ids["one"], key="job-cancel")
    override_principal(app, ids["one"])

    continued = client.post(
        f"/api/v1/jobs/{paused['job_id']}/actions",
        json={
            "required_action_id": paused["action_id"],
            "action": "CONTINUE_LIMITED",
            "expected_input_version": "analysis-v1",
            "expected_result_version": "result-v3",
        },
        headers={"Idempotency-Key": "job-continue-request"},
    )
    cancelled = client.post(
        f"/api/v1/jobs/{queued_job_id}/cancel",
        headers={"Idempotency-Key": "job-cancel-request"},
    )
    stopped = client.post(
        f"/api/v1/jobs/{stopped_job['job_id']}/actions",
        json={
            "required_action_id": stopped_job["action_id"],
            "action": "STOP",
            "expected_input_version": "analysis-v1",
            "expected_result_version": "result-v3",
        },
        headers={"Idempotency-Key": "job-stop-request"},
    )

    assert continued.status_code == 202
    assert continued.json()["status"] == "QUEUED"
    assert continued.json()["dispatch_status"] == "OUTBOX_PENDING"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["dispatch_status"] == "INVALIDATED"
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "CANCELLED"
