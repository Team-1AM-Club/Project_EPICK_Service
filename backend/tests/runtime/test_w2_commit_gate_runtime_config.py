from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w2-commit-gate"
DLQ_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w2-commit-gate-dlq"
REGULAR_COMMAND_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w2-command"
CT15_GATE_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-ct15-w2-gate"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "w2_commit_gate_inbound_queue_url": QUEUE_URL,
        "w2_commit_gate_inbound_dlq_url": DLQ_URL,
        "w2_commit_gate_expected_producer": "w2",
        "w2_commit_gate_expected_sender_id": "AROAW2PRODUCER",
        "w2_commit_gate_batch_size": 10,
        "w2_commit_gate_wait_seconds": 20,
        "w2_commit_gate_visibility_seconds": 120,
        "w2_collection_command_queue_url": REGULAR_COMMAND_QUEUE_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_w2_commit_gate_runtime_accepts_bounded_dedicated_settings() -> None:
    settings = _settings()

    assert settings.w2_commit_gate_inbound_queue_url == QUEUE_URL
    assert settings.w2_commit_gate_inbound_dlq_url == DLQ_URL
    assert settings.w2_commit_gate_expected_producer == "w2"
    assert settings.w2_commit_gate_outbound_queue_url == REGULAR_COMMAND_QUEUE_URL


def test_ct15_gate_only_route_requires_explicit_approval_and_stays_separate() -> None:
    settings = _settings(
        w2_ct15_gate_only_queue_approved=True,
        w2_ct15_gate_only_command_queue_url=CT15_GATE_QUEUE_URL,
    )

    assert settings.w2_commit_gate_outbound_queue_url == CT15_GATE_QUEUE_URL


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"w2_commit_gate_inbound_queue_url": None}, "configured together"),
        ({"w2_commit_gate_inbound_dlq_url": QUEUE_URL}, "must be distinct"),
        ({"w2_commit_gate_expected_sender_id": None}, "expected sender id"),
        ({"w2_commit_gate_expected_producer": "untrusted-body"}, "must be w2"),
        ({"w2_commit_gate_batch_size": 0}, "batch size"),
        ({"w2_commit_gate_batch_size": 11}, "batch size"),
        ({"w2_commit_gate_wait_seconds": -1}, "wait seconds"),
        ({"w2_commit_gate_wait_seconds": 21}, "wait seconds"),
        ({"w2_commit_gate_visibility_seconds": 0}, "visibility seconds"),
        ({"w2_commit_gate_visibility_seconds": 43_201}, "visibility seconds"),
        ({"w2_ct15_gate_only_queue_approved": True}, "CT15 gate-only queue"),
        ({"w2_ct15_gate_only_command_queue_url": CT15_GATE_QUEUE_URL}, "explicit approval"),
        (
            {
                "w2_ct15_gate_only_queue_approved": True,
                "w2_ct15_gate_only_command_queue_url": REGULAR_COMMAND_QUEUE_URL,
            },
            "must differ",
        ),
        (
            {
                "w2_ct15_gate_only_queue_approved": True,
                "w2_ct15_gate_only_command_queue_url": "https://sqs.example/gate",
            },
            "identify the CT15",
        ),
    ],
)
def test_w2_commit_gate_runtime_rejects_missing_or_unsafe_settings(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(**overrides)
