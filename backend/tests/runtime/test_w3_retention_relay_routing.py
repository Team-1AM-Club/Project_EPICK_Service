from __future__ import annotations

import pytest

from app.runtime.outbox_relay import (
    W3_OWNER_DELETION_MESSAGE_TYPE,
    W3_SOURCE_RETIREMENT_MESSAGE_TYPE,
    QueueUrlRegistry,
)

COMMAND_QUEUE = "https://sqs.ap-northeast-2.amazonaws.com/123/w3-retention-command"


def test_retention_only_registry_claims_only_w3_retention_commands() -> None:
    registry = QueueUrlRegistry(
        w1_execution_queue_url=None,
        w3_retention_command_queue_url=COMMAND_QUEUE,
        retention_only=True,
    )

    assert registry.supported_message_types == (
        W3_OWNER_DELETION_MESSAGE_TYPE,
        W3_SOURCE_RETIREMENT_MESSAGE_TYPE,
    )
    for message_type in registry.supported_message_types:
        _, queue_url = registry.resolve(message_type=message_type)
        assert queue_url == COMMAND_QUEUE


def test_retention_only_registry_requires_its_dedicated_queue() -> None:
    with pytest.raises(ValueError, match="dedicated W3 command queue"):
        QueueUrlRegistry(
            w1_execution_queue_url=None,
            retention_only=True,
        )


def test_dedicated_relay_scopes_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="at most one dedicated route"):
        QueueUrlRegistry(
            w1_execution_queue_url=None,
            w2_commit_gate_command_queue_url="https://sqs.example/w2-gate",
            w3_retention_command_queue_url=COMMAND_QUEUE,
            commit_gate_only=True,
            retention_only=True,
        )
