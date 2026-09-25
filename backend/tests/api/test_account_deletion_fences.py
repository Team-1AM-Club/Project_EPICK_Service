from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.session import set_local_owner_context
from app.models.deletion import DeletionTarget
from app.models.identity import User
from app.models.jobs import Job, OutboxMessage
from app.repo.deletion import DeletionRepository
from app.services.deletion import DeletionOrchestrationService
from tests.api.conftest import override_principal
from tests.api.job_support import accept_job


def _start_account_deletion(client: TestClient) -> tuple[dict[str, object], dict[str, object]]:
    preview = client.post("/api/v1/account/deletion-previews")
    assert preview.status_code == 201
    body = preview.json()
    confirmation = {
        "deletion_request_id": body["deletion_request_id"],
        "preview_token": body["preview_token"],
        "confirmation": "DELETE",
    }
    accepted = client.post(
        "/api/v1/account/deletion-requests",
        json=confirmation,
        headers={"Idempotency-Key": "account-deletion-start"},
    )
    assert accepted.status_code == 202
    return body, accepted.json()


@pytest.mark.postgres
def test_account_deletion_fences_jobs_stages_targets_and_retries_only_failed_target(
    job_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = job_api_client
    active_job_id = accept_job(factory, owner_user_id=ids["one"], key="account-deletion-job")
    override_principal(app, ids["one"])
    preview, accepted = _start_account_deletion(client)
    request_id = UUID(str(preview["deletion_request_id"]))

    with factory.begin() as session:
        set_local_owner_context(session, ids["one"])
        owner = session.get(User, ids["one"])
        request = DeletionRepository(session).get_request_for_update(
            deletion_request_id=request_id, owner_user_id=ids["one"]
        )
        assert owner is not None and owner.deletion_epoch == 1
        assert request is not None and request.status == "RUNNING"
        target = session.scalar(
            select(DeletionTarget)
            .where(DeletionTarget.deletion_request_id == request_id)
            .order_by(DeletionTarget.store_type)
            .limit(1)
        )
        assert target is not None
        deletion_outbox = list(
            session.scalars(
                select(OutboxMessage).where(OutboxMessage.deletion_request_id == request_id)
            )
        )
        assert len(deletion_outbox) == 7
        assert {message.deletion_target_id for message in deletion_outbox} == {
            item.id
            for item in session.scalars(
                select(DeletionTarget).where(DeletionTarget.deletion_request_id == request_id)
            )
        }
        assert {message.visibility_scope for message in deletion_outbox} == {"PRIVATE"}
        assert {message.owner_deletion_epoch for message in deletion_outbox} == {1}
        job = session.get(Job, UUID(active_job_id))
        assert job is not None
        assert job.status == "CANCELLED"
        assert job.dispatch_status == "INVALIDATED"
        assert job.owner_deletion_epoch == 1
        DeletionOrchestrationService(session).record_target_failure(
            owner_user_id=ids["one"],
            deletion_request_id=request_id,
            deletion_target_id=target.id,
            failure_code="STORE_TIMEOUT",
        )
        target_id = target.id

    failed = client.get(f"/api/v1/account/deletion-requests/{request_id}")
    assert failed.status_code == 200
    assert failed.json()["status"] == "FAILED_RETRYABLE"
    assert failed.json()["completed_at"] is None

    retried = client.post(
        f"/api/v1/account/deletion-requests/{request_id}/retry",
        json={"target_id": str(target_id)},
        headers={"Idempotency-Key": "account-deletion-retry"},
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "RUNNING"
    retried_target = next(
        item for item in retried.json()["targets"] if item["id"] == str(target_id)
    )
    assert retried_target["status"] == "QUEUED"
    assert retried.json()["completed_at"] is None

    retried_replay = client.post(
        f"/api/v1/account/deletion-requests/{request_id}/retry",
        json={"target_id": str(target_id)},
        headers={"Idempotency-Key": "account-deletion-retry"},
    )
    assert retried_replay.status_code == 202
    assert retried_replay.json() == retried.json()

    reused_token = client.post(
        "/api/v1/account/deletion-requests",
        json={
            "deletion_request_id": str(request_id),
            "preview_token": preview["preview_token"],
            "confirmation": "DELETE",
        },
        headers={"Idempotency-Key": "account-deletion-token-reuse"},
    )
    assert reused_token.status_code == 409

    override_principal(app, ids["two"])
    assert (
        client.post(
            f"/api/v1/account/deletion-requests/{request_id}/retry",
            json={"target_id": str(target_id)},
            headers={"Idempotency-Key": "account-deletion-foreign-retry"},
        ).status_code
        == 404
    )
