from __future__ import annotations

import pytest

from app.runtime.outbox_relay import QueueUrlRegistry

EXECUTION_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w1-execution"
REGULAR_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w2-regular"
GATE_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/ct15-w2-gate"
COMMIT_GATE_MESSAGE_TYPE = "w1.private.w2.commit-gate.v1"


def test_ct15_gate_only_registry_selects_only_commit_gate_messages() -> None:
    registry = QueueUrlRegistry(
        w1_execution_queue_url=EXECUTION_QUEUE_URL,
        w2_collection_command_queue_url=REGULAR_QUEUE_URL,
        w2_commit_gate_command_queue_url=GATE_QUEUE_URL,
        commit_gate_only=True,
    )

    route, queue_url = registry.resolve(message_type=COMMIT_GATE_MESSAGE_TYPE)

    assert registry.supported_message_types == (COMMIT_GATE_MESSAGE_TYPE,)
    assert route.message_type == COMMIT_GATE_MESSAGE_TYPE
    assert queue_url == GATE_QUEUE_URL


def test_normal_registry_retains_existing_commit_gate_queue_fallback() -> None:
    registry = QueueUrlRegistry(
        w1_execution_queue_url=EXECUTION_QUEUE_URL,
        w2_collection_command_queue_url=REGULAR_QUEUE_URL,
    )

    _, queue_url = registry.resolve(message_type=COMMIT_GATE_MESSAGE_TYPE)

    assert queue_url == REGULAR_QUEUE_URL


def test_gate_only_registry_refuses_a_missing_dedicated_queue() -> None:
    with pytest.raises(ValueError, match="dedicated commit-gate queue"):
        QueueUrlRegistry(
            w1_execution_queue_url=EXECUTION_QUEUE_URL,
            w2_collection_command_queue_url=REGULAR_QUEUE_URL,
            commit_gate_only=True,
        )
