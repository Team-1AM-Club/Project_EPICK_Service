from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas.jobs import JobResponse
from app.main import create_app
from tests.fixtures.frontend_jobs import public_job_payload


def test_public_job_openapi_freezes_all_lifecycle_dispatch_and_action_enums() -> None:
    schemas = create_app().openapi()["components"]["schemas"]

    assert schemas["JobResponse"]["properties"]["status"]["enum"] == [
        "QUEUED",
        "RUNNING",
        "WAITING_USER",
        "PAUSED_RATE_LIMIT",
        "SUCCEEDED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "CANCEL_REQUESTED",
        "CANCELLED",
    ]
    assert schemas["JobResponse"]["properties"]["dispatch_status"]["enum"] == [
        "OUTBOX_PENDING",
        "ENQUEUED",
        "CLAIMED",
        "BLOCKED",
        "INVALIDATED",
    ]
    assert schemas["JobResponse"]["properties"]["completeness"]["enum"] == [
        "none",
        "partial",
        "complete",
    ]
    assert schemas["JobRequiredActionResponse"]["properties"]["code"]["enum"] == [
        "RETRY",
        "CONTINUE_LIMITED",
        "STOP",
    ]


@pytest.mark.parametrize(
    "private_field",
    [
        "owner_user_id",
        "command_id",
        "execution_fence",
        "owner_deletion_epoch",
        "active_lease_id",
        "queue_url",
        "worker_retry_count",
    ],
)
def test_public_job_response_rejects_private_worker_fields(private_field: str) -> None:
    payload = public_job_payload()
    payload[private_field] = "private"

    with pytest.raises(ValidationError):
        JobResponse.model_validate(payload)
