from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

COMMAND_QUEUE = "https://sqs.ap-northeast-2.amazonaws.com/123/w3-retention-command"
RECEIPT_QUEUE = "https://sqs.ap-northeast-2.amazonaws.com/123/w3-retention-receipt"
RECEIPT_DLQ = "https://sqs.ap-northeast-2.amazonaws.com/123/w3-retention-receipt-dlq"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "w3_retention_command_queue_url": COMMAND_QUEUE,
        "w3_retention_receipt_queue_url": RECEIPT_QUEUE,
        "w3_retention_receipt_dlq_url": RECEIPT_DLQ,
        "w3_retention_w1_stable_role_id": "AROAW1RETENTION",
        "w3_retention_expected_w3_sender_id": "AROAW3RETENTION",
        "deletion_worker_database_url": "postgresql+psycopg://deleter:secret@db/deletions",
        "w3_retention_receipt_batch_size": 10,
        "w3_retention_receipt_wait_seconds": 20,
        "w3_retention_receipt_visibility_seconds": 120,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_w3_retention_runtime_accepts_complete_bounded_private_settings() -> None:
    settings = _settings()

    assert settings.w3_retention_command_queue_url == COMMAND_QUEUE
    assert settings.w3_retention_receipt_queue_url == RECEIPT_QUEUE
    assert settings.w3_retention_expected_w3_sender_id == "AROAW3RETENTION"


def test_w3_command_relay_can_receive_only_its_queue_setting() -> None:
    settings = Settings(
        _env_file=None,
        w3_retention_command_queue_url=COMMAND_QUEUE,
    )

    assert settings.w3_retention_command_queue_url == COMMAND_QUEUE


def test_w3_receipt_worker_does_not_need_command_queue_or_w1_role_id() -> None:
    settings = Settings(
        _env_file=None,
        w3_retention_receipt_queue_url=RECEIPT_QUEUE,
        w3_retention_receipt_dlq_url=RECEIPT_DLQ,
        w3_retention_expected_w3_sender_id="AROAW3RETENTION",
        deletion_worker_database_url="postgresql+psycopg://deleter:secret@db/deletions",
    )

    assert settings.w3_retention_command_queue_url is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"w3_retention_receipt_queue_url": None}, "configured together"),
        ({"w3_retention_receipt_dlq_url": RECEIPT_QUEUE}, "must differ"),
        ({"w3_retention_w1_stable_role_id": "AROAW1:session"}, "stable IDs"),
        ({"w3_retention_expected_w3_sender_id": "AROAW3:session"}, "stable IDs"),
        ({"w3_retention_receipt_batch_size": 0}, "batch size"),
        ({"w3_retention_receipt_batch_size": 11}, "batch size"),
        ({"w3_retention_receipt_wait_seconds": -1}, "wait seconds"),
        ({"w3_retention_receipt_wait_seconds": 21}, "wait seconds"),
        ({"w3_retention_receipt_visibility_seconds": 0}, "visibility seconds"),
        ({"w3_retention_receipt_visibility_seconds": 43_201}, "visibility seconds"),
    ],
)
def test_w3_retention_runtime_rejects_partial_or_unsafe_settings(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(**overrides)
