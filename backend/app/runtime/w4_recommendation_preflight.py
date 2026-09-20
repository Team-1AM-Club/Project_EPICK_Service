from __future__ import annotations

import json
import re
from dataclasses import dataclass

_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class W4RecommendationPreflightError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class W4RecommendationPreflightInput:
    w1_image: str
    w4_image: str
    source_revision: str
    schema_manifest_sha256: str
    actual_schema_manifest_sha256: str
    w1_sender_role_id: str
    w4_worker_role_id: str
    main_queue_arn: str
    dlq_arn: str
    redrive_policy: str
    main_sse_enabled: bool
    dlq_sse_enabled: bool
    visibility_seconds: int
    heartbeat_seconds: int
    request_timeout_seconds: int
    private_health_status: str
    private_database_status: str
    real_data_enabled: bool
    synthetic_acceptance_enabled: bool


def verify_w4_recommendation_runtime(
    value: W4RecommendationPreflightInput,
) -> dict[str, str]:
    if "@sha256:" not in value.w1_image or "@sha256:" not in value.w4_image:
        raise W4RecommendationPreflightError("both runtime images must use immutable digests")
    if not _SHA.fullmatch(value.source_revision):
        raise W4RecommendationPreflightError("W4 source revision is not a full SHA")
    if not _DIGEST.fullmatch(value.schema_manifest_sha256):
        raise W4RecommendationPreflightError("configured schema manifest digest is invalid")
    if value.schema_manifest_sha256 != value.actual_schema_manifest_sha256:
        raise W4RecommendationPreflightError("adopted schema manifest digest does not match")
    if not value.w1_sender_role_id or not value.w4_worker_role_id:
        raise W4RecommendationPreflightError("workload role IDs are required")
    if value.w1_sender_role_id == value.w4_worker_role_id:
        raise W4RecommendationPreflightError("W1 sender and W4 worker identities must differ")
    if not value.main_sse_enabled or not value.dlq_sse_enabled:
        raise W4RecommendationPreflightError("Main Queue and DLQ encryption must be enabled")
    try:
        redrive = json.loads(value.redrive_policy)
    except json.JSONDecodeError as error:
        raise W4RecommendationPreflightError("Main Queue redrive policy is invalid") from error
    if redrive.get("deadLetterTargetArn") != value.dlq_arn:
        raise W4RecommendationPreflightError("Main Queue is not bound to the expected DLQ")
    if int(redrive.get("maxReceiveCount", 0)) != 5:
        raise W4RecommendationPreflightError("Main Queue maxReceiveCount must be 5")
    if not (value.request_timeout_seconds < value.heartbeat_seconds < value.visibility_seconds):
        raise W4RecommendationPreflightError(
            "HTTP timeout, heartbeat, and visibility bounds are unsafe"
        )
    if value.private_health_status != "ok" or value.private_database_status != "worker_accessible":
        raise W4RecommendationPreflightError("W1 private RunStore health check failed")
    if value.real_data_enabled:
        raise W4RecommendationPreflightError("REAL W4 recommendation data is not approved")
    if not value.synthetic_acceptance_enabled:
        raise W4RecommendationPreflightError("T104 synthetic acceptance must be explicitly enabled")
    return {
        "status": "ok",
        "images": "immutable_digests_configured",
        "source_and_schema": "pinned_and_verified",
        "identities": "distinct",
        "queue": "encrypted_redrive_bound",
        "private_http": "authenticated_database_accessible",
        "real_data": "disabled",
        "synthetic_acceptance": "explicitly_enabled",
    }
