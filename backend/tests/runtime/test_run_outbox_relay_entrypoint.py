from __future__ import annotations

from unittest.mock import Mock, patch

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
