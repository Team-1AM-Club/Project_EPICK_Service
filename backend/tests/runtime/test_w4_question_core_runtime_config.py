from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-isolated-w4-question-core"
DLQ_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-isolated-w4-question-core-dlq"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "w4_question_core_decision_queue_url": QUEUE_URL,
        "w4_question_core_decision_dlq_url": DLQ_URL,
        "w4_question_core_decision_expected_producer": "w4",
        "w4_question_core_decision_expected_sender_id": "AROAW4PRODUCER",
        "w4_question_core_decision_batch_size": 10,
        "w4_question_core_decision_wait_seconds": 20,
        "w4_question_core_decision_visibility_seconds": 120,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_w4_question_core_runtime_accepts_bounded_dedicated_settings() -> None:
    settings = _settings()

    assert settings.w4_question_core_decision_queue_url == QUEUE_URL
    assert settings.w4_question_core_decision_dlq_url == DLQ_URL
    assert settings.w4_question_core_decision_expected_producer == "w4"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"w4_question_core_decision_queue_url": None}, "configured together"),
        ({"w4_question_core_decision_dlq_url": QUEUE_URL}, "must be distinct"),
        ({"w4_question_core_decision_expected_sender_id": None}, "expected sender id"),
        (
            {"w4_question_core_decision_expected_sender_id": "AROAW4PRODUCER:session"},
            "without session",
        ),
        ({"w4_question_core_decision_expected_producer": "untrusted-body"}, "must be w4"),
        ({"w4_question_core_decision_batch_size": 0}, "batch size"),
        ({"w4_question_core_decision_batch_size": 11}, "batch size"),
        ({"w4_question_core_decision_wait_seconds": -1}, "wait seconds"),
        ({"w4_question_core_decision_wait_seconds": 21}, "wait seconds"),
        ({"w4_question_core_decision_visibility_seconds": 0}, "visibility seconds"),
        ({"w4_question_core_decision_visibility_seconds": 43_201}, "visibility seconds"),
    ],
)
def test_w4_question_core_runtime_rejects_missing_or_unsafe_settings(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(**overrides)
