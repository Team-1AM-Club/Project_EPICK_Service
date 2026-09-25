from __future__ import annotations

import pytest

from app.runtime.outbox_relay import QueueUrlRegistry


def test_v2_deletion_route_requires_a_dedicated_queue_and_no_fallback() -> None:
    with pytest.raises(ValueError):
        QueueUrlRegistry(
            w1_execution_queue_url=None,
            w2_collection_command_queue_url="collection-queue",
            deletion_only=True,
        )
    registry = QueueUrlRegistry(
        w1_execution_queue_url=None,
        w2_collection_command_queue_url="collection-queue",
        w2_deletion_command_queue_url="deletion-queue",
        deletion_only=True,
    )
    assert registry.supported_message_types == ("w1.private.w2.deletion-command.v2",)
    assert registry.resolve(message_type="w1.private.w2.deletion-command.v2")[1] == (
        "deletion-queue"
    )
