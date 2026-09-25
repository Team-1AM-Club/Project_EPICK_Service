from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scripts import run_outbox_relay


def test_build_relay_loads_full_model_registry_before_creating_session() -> None:
    order: list[str] = []
    session_factory = object()

    with (
        patch.object(
            run_outbox_relay,
            "load_all_models",
            side_effect=lambda: order.append("models"),
        ),
        patch.object(
            run_outbox_relay,
            "create_worker_session_factory",
            side_effect=lambda: (order.append("session"), session_factory)[1],
        ),
        patch.object(run_outbox_relay, "Boto3SqsPort", return_value=object()),
        patch.object(run_outbox_relay, "OutboxRelay", return_value=Mock()) as relay,
        patch.object(
            run_outbox_relay.settings,
            "w4_recommendation_execution_queue_url",
            "https://sqs.ap-northeast-2.amazonaws.com/example/recommendation",
        ),
    ):
        run_outbox_relay._build_relay()

    assert order == ["models", "session"]
    assert relay.call_args.kwargs["session_factory"] is session_factory


def test_w2_deletion_scope_refuses_missing_activation_proof() -> None:
    deletion_factory = object()
    with (
        patch.object(run_outbox_relay, "load_all_models"),
        patch.object(run_outbox_relay, "create_worker_session_factory") as general_factory,
        patch.object(
            run_outbox_relay,
            "create_deletion_worker_session_factory",
            return_value=deletion_factory,
        ),
        patch.object(run_outbox_relay, "Boto3SqsPort", return_value=object()),
        patch.object(run_outbox_relay, "OutboxRelay", return_value=Mock()) as relay,
        patch.object(run_outbox_relay.settings, "w2_deletion_command_queue_url", "w2-delete"),
    ):
        with pytest.raises(SystemExit, match="W2 deletion activation proof"):
            run_outbox_relay._build_relay(scope="w2-deletion")
    general_factory.assert_not_called()
    relay.assert_not_called()


def test_w2_deletion_scope_uses_proven_route_and_deletion_principal(tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    proof_path = tmp_path / "w2-activation.json"
    proof_path.write_text(
        json.dumps(
            {
                "schema_version": "w1.w2-deletion-activation.v1",
                "w2_source_sha": "e2491a4084ed50090d5135ba177a3772e9a44f5a",
                "w2_migration_head": "0010_private_deletion_scope_v2",
                "w2_image_digest": digest,
                "command_schema_sha256": (
                    "73eb3a51d15923969d483b13fdbf498e0a6cd8cfa18c019a66f5f532cfd64067"
                ),
                "ack_schema_sha256": (
                    "22cd9cd44061b5c14c9852634417482993a8ffb8f69dc8e98e3b6cd71b891bc9"
                ),
                "checked_at": datetime.now(UTC).isoformat(timespec="seconds").replace(
                    "+00:00", "Z"
                ),
            }
        ),
        encoding="utf-8",
    )
    deletion_factory = object()
    with (
        patch.object(run_outbox_relay, "load_all_models"),
        patch.object(run_outbox_relay, "create_worker_session_factory") as general_factory,
        patch.object(
            run_outbox_relay,
            "create_deletion_worker_session_factory",
            return_value=deletion_factory,
        ),
        patch.object(run_outbox_relay, "Boto3SqsPort", return_value=object()),
        patch.object(run_outbox_relay, "OutboxRelay", return_value=Mock()) as relay,
        patch.object(run_outbox_relay.settings, "w2_deletion_command_queue_url", "w2-delete"),
        patch.object(
            run_outbox_relay.settings, "w2_deletion_activation_proof_path", str(proof_path)
        ),
        patch.object(run_outbox_relay.settings, "w2_deletion_image_digest", digest),
    ):
        run_outbox_relay._build_relay(scope="w2-deletion")
    general_factory.assert_not_called()
    assert relay.call_args.kwargs["session_factory"] is deletion_factory
    assert relay.call_args.kwargs["queues"].supported_message_types == (
        "w1.private.w2.deletion-command.v2",
    )
