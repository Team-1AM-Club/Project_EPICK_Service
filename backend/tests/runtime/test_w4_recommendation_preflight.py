from __future__ import annotations

import json

import pytest

from app.runtime.w4_recommendation_preflight import (
    W4RecommendationPreflightError,
    W4RecommendationPreflightInput,
    verify_w4_recommendation_runtime,
)


def _input(**changes) -> W4RecommendationPreflightInput:
    values = {
        "w1_image": "registry/w1@sha256:" + "a" * 64,
        "w4_image": "registry/w4@sha256:" + "b" * 64,
        "source_revision": "d" * 40,
        "schema_manifest_sha256": "sha256:" + "c" * 64,
        "actual_schema_manifest_sha256": "sha256:" + "c" * 64,
        "w1_sender_role_id": "AROA-W1",
        "w4_worker_role_id": "AROA-W4",
        "main_queue_arn": "arn:aws:sqs:region:account:main",
        "dlq_arn": "arn:aws:sqs:region:account:dlq",
        "redrive_policy": json.dumps(
            {
                "deadLetterTargetArn": "arn:aws:sqs:region:account:dlq",
                "maxReceiveCount": 5,
            }
        ),
        "main_sse_enabled": True,
        "dlq_sse_enabled": True,
        "visibility_seconds": 600,
        "heartbeat_seconds": 60,
        "request_timeout_seconds": 30,
        "private_health_status": "ok",
        "private_database_status": "worker_accessible",
        "real_data_enabled": False,
        "synthetic_acceptance_enabled": True,
    }
    values.update(changes)
    return W4RecommendationPreflightInput(**values)


def test_preflight_returns_only_safe_statuses() -> None:
    result = verify_w4_recommendation_runtime(_input())
    assert result["status"] == "ok"
    assert result["real_data"] == "disabled"
    assert "arn:" not in json.dumps(result)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"w4_worker_role_id": "AROA-W1"}, "identities"),
        ({"real_data_enabled": True}, "REAL"),
        ({"synthetic_acceptance_enabled": False}, "explicitly enabled"),
        ({"visibility_seconds": 45}, "bounds"),
        ({"main_sse_enabled": False}, "encryption"),
        ({"actual_schema_manifest_sha256": "sha256:" + "e" * 64}, "digest"),
    ],
)
def test_preflight_fails_closed(changes, message) -> None:
    with pytest.raises(W4RecommendationPreflightError, match=message):
        verify_w4_recommendation_runtime(_input(**changes))
