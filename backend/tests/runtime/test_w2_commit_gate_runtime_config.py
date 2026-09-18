from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w2-commit-gate"
DLQ_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w2-commit-gate-dlq"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "w2_commit_gate_inbound_queue_url": QUEUE_URL,
        "w2_commit_gate_inbound_dlq_url": DLQ_URL,
        "w2_commit_gate_expected_producer": "w2",
        "w2_commit_gate_expected_sender_id": "AROAW2PRODUCER",
        "w2_commit_gate_batch_size": 10,
        "w2_commit_gate_wait_seconds": 20,
        "w2_commit_gate_visibility_seconds": 120,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_w2_commit_gate_runtime_accepts_bounded_dedicated_settings() -> None:
    settings = _settings()

    assert settings.w2_commit_gate_inbound_queue_url == QUEUE_URL
    assert settings.w2_commit_gate_inbound_dlq_url == DLQ_URL
    assert settings.w2_commit_gate_expected_producer == "w2"


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
    ],
)
def test_w2_commit_gate_runtime_rejects_missing_or_unsafe_settings(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(**overrides)
