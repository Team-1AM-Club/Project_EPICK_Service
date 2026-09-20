from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from app.models.jobs import OutboxMessage
from app.models.recommendation_execution import RecommendationExecutionBinding
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.runtime.w4_recommendation_worker import classify_w4_failure


def test_recommendation_dispatch_is_reference_only_and_routes_to_dedicated_queue() -> None:
    run_id = uuid4()
    binding = RecommendationExecutionBinding(
        id=uuid4(),
        run_id=run_id,
        owner_user_id=uuid4(),
        project_id=uuid4(),
        question_id=uuid4(),
        question_version_id=uuid4(),
        snapshot_id=uuid4(),
        owner_deletion_epoch=0,
        context_sha256="sha256:" + "a" * 64,
        contract_version="w1-w4-recommendation/1.0",
        engine_source_revision="df41433218918e4167784243dc9b88e5a858278d",
        request_body={},
        context_body={},
    )
    message = OutboxMessage(
        id=uuid4(),
        message_type="w1.private.w4.recommendation-execution.v1",
        schema_version="w1.w4.recommendation-exec/1",
        visibility_scope="PRIVATE",
        aggregate_type="recommendation_run",
        aggregate_id=run_id,
        aggregate_revision=1,
        recommendation_run_id=run_id,
        owner_user_id=binding.owner_user_id,
        owner_deletion_epoch=0,
        payload={
            "run_id": str(run_id),
            "binding_id": str(binding.id),
            "contract_version": binding.contract_version,
            "engine_source_revision": binding.engine_source_revision,
            "schema_manifest_sha256": "sha256:" + "b" * 64,
        },
    )
    body = json.loads(
        OutboxRelay._serialize_w4_recommendation_dispatch(
            message=message,
            binding=binding,
            issued_at=datetime.now(UTC),
        )
    )

    assert body["run_id"] == str(run_id)
    assert body["binding_id"] == str(binding.id)
    assert not {
        "context",
        "episode",
        "question_text",
        "source_url",
        "lease_token",
        "model_prompt",
    }.intersection(body)
    registry = QueueUrlRegistry(
        w1_execution_queue_url=None,
        w4_recommendation_execution_queue_url="https://sqs.example/w4-recommendation",
    )
    _, queue_url = registry.resolve(message_type=message.message_type)
    assert queue_url.endswith("/w4-recommendation")


def test_engine_failure_policy_never_allows_synthetic_fallback() -> None:
    assert classify_w4_failure("W1_PRIVATE_TRANSPORT_RETRYABLE").action == "RETRY"
    terminal = classify_w4_failure("W4_SCHEMA_INVALID")
    assert terminal.action == "ACK_TERMINAL"
    assert terminal.synthetic_fallback_allowed is False
