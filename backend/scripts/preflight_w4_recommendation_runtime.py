from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

import boto3

from app.runtime.w4_recommendation_preflight import (
    W4RecommendationPreflightInput,
    verify_w4_recommendation_runtime,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


def main() -> None:
    region = _required("AWS_DEFAULT_REGION")
    main_url = _required("W4_RECOMMENDATION_EXECUTION_QUEUE_URL")
    dlq_url = _required("W4_RECOMMENDATION_EXECUTION_DLQ_URL")
    sqs = boto3.client("sqs", region_name=region)
    main_attributes = sqs.get_queue_attributes(
        QueueUrl=main_url,
        AttributeNames=[
            "QueueArn",
            "RedrivePolicy",
            "SqsManagedSseEnabled",
            "VisibilityTimeout",
        ],
    )["Attributes"]
    dlq_attributes = sqs.get_queue_attributes(
        QueueUrl=dlq_url,
        AttributeNames=["QueueArn", "SqsManagedSseEnabled"],
    )["Attributes"]
    worker_role_id = (
        boto3.client("sts", region_name=region).get_caller_identity()["UserId"].split(":", 1)[0]
    )

    base_url = _required("W4_RECOMMENDATION_PRIVATE_BASE_URL").rstrip("/")
    request = Request(
        f"{base_url}/internal/v1/w4/recommendations/health",
        headers={
            "Authorization": f"Bearer {_required('W4_RECOMMENDATION_PRIVATE_BEARER')}",
            "X-EPICK-Service-Principal": _required("W4_RECOMMENDATION_PRIVATE_AUDIENCE"),
        },
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - operator-provided private URL
        health = json.loads(response.read().decode("utf-8"))

    manifest_path = Path(_required("W4_RECOMMENDATION_SCHEMA_MANIFEST_PATH"))
    manifest_digest = "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    result = verify_w4_recommendation_runtime(
        W4RecommendationPreflightInput(
            w1_image=_required("BACKEND_IMAGE"),
            w4_image=_required("W4_RECOMMENDATION_IMAGE"),
            source_revision=_required("W4_RECOMMENDATION_ENGINE_SOURCE_REVISION"),
            schema_manifest_sha256=_required("W4_RECOMMENDATION_SCHEMA_MANIFEST_SHA256"),
            actual_schema_manifest_sha256=manifest_digest,
            w1_sender_role_id=_required("W1_RECOMMENDATION_SENDER_ROLE_ID"),
            w4_worker_role_id=worker_role_id,
            main_queue_arn=main_attributes["QueueArn"],
            dlq_arn=dlq_attributes["QueueArn"],
            redrive_policy=main_attributes["RedrivePolicy"],
            main_sse_enabled=main_attributes.get("SqsManagedSseEnabled") == "true",
            dlq_sse_enabled=dlq_attributes.get("SqsManagedSseEnabled") == "true",
            visibility_seconds=int(main_attributes["VisibilityTimeout"]),
            heartbeat_seconds=int(_required("W4_RECOMMENDATION_HEARTBEAT_SECONDS")),
            request_timeout_seconds=int(_required("W4_RECOMMENDATION_REQUEST_TIMEOUT_SECONDS")),
            private_health_status=str(health.get("status")),
            private_database_status=str(health.get("database")),
            real_data_enabled=(
                os.environ.get("W4_RECOMMENDATION_REAL_DATA_ENABLED", "false").lower() == "true"
            ),
            synthetic_acceptance_enabled=(
                os.environ.get("W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE") == "YES"
            ),
        )
    )
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
