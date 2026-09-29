from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from epick_engine.source_collection import private_deletion_operator_v2 as operator
from epick_engine.source_collection.commit_gate_runtime import ConsumeResult
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionAckV2


def environment() -> dict[str, str]:
    return {
        "EPICK_DATABASE_URL": "postgresql+psycopg://w2:secret@db.example/epick",
        "W2_PRIVATE_DELETION_REGION": "ap-northeast-2",
        "W1_PRIVATE_DELETION_COMMAND_QUEUE_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/w1-private-deletion-v2"
        ),
        "W1_PRIVATE_DELETION_ACK_ENDPOINT": (
            "https://w1.example.test/internal/v1/w2-private/deletion/ack"
        ),
        "W1_PRIVATE_DELETION_ACK_BEARER": "synthetic-bearer",
        "W1_PRIVATE_DELETION_ACK_CA_FILE": "/run/epick/private-deletion/w1-ca.pem",
        "W1_EXPECTED_SYSTEM_SENDER_ID": "AROASYNTHETICW1ROLE12",
    }


@pytest.mark.parametrize("missing", list(environment()))
def test_settings_require_every_explicit_environment_value(missing: str) -> None:
    values = environment()
    values.pop(missing)

    with pytest.raises(operator.PrivateDeletionOperatorConfigurationError):
        operator.PrivateDeletionOperatorSettings.from_environment(values)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("EPICK_DATABASE_URL", "sqlite:///local.db"),
        ("W2_PRIVATE_DELETION_REGION", ""),
        (
            "W1_PRIVATE_DELETION_COMMAND_QUEUE_URL",
            "http://sqs.ap-northeast-2.amazonaws.com/123456789012/private-deletion",
        ),
        (
            "W1_PRIVATE_DELETION_ACK_ENDPOINT",
            "https://w1.example.test/internal/v1/w2-private/deletion/not-ack",
        ),
        ("W1_PRIVATE_DELETION_ACK_BEARER", ""),
        ("W1_EXPECTED_SYSTEM_SENDER_ID", "arn:aws:iam::123456789012:role/w1"),
    ],
)
def test_settings_reject_unsafe_or_ambiguous_values(key: str, value: str) -> None:
    values = environment()
    values[key] = value

    with pytest.raises(operator.PrivateDeletionOperatorConfigurationError):
        operator.PrivateDeletionOperatorSettings.from_environment(values)


@pytest.mark.parametrize("region", ("us-gov-west-1", "cn-north-1", "us-iso-east-1"))
def test_settings_reject_noncommercial_aws_partition_regions(region: str) -> None:
    values = environment()
    values["W2_PRIVATE_DELETION_REGION"] = region
    values["W1_PRIVATE_DELETION_COMMAND_QUEUE_URL"] = (
        f"https://sqs.{region}.amazonaws.com/123456789012/w1-private-deletion-v2"
    )

    with pytest.raises(operator.PrivateDeletionOperatorConfigurationError):
        operator.PrivateDeletionOperatorSettings.from_environment(values)


@pytest.mark.parametrize("region", ("us-east-1", "eu-west-1", "ap-southeast-2"))
def test_settings_preserve_supported_commercial_aws_regions(region: str) -> None:
    values = environment()
    values["W2_PRIVATE_DELETION_REGION"] = region
    values["W1_PRIVATE_DELETION_COMMAND_QUEUE_URL"] = (
        f"https://sqs.{region}.amazonaws.com/123456789012/w1-private-deletion-v2"
    )

    settings = operator.PrivateDeletionOperatorSettings.from_environment(values)

    assert settings.region == region


@pytest.mark.parametrize(
    "credential_name",
    (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
    ),
)
def test_settings_reject_static_aws_credential_configuration(credential_name: str) -> None:
    values = environment()
    values[credential_name] = "must-not-be-used"

    with pytest.raises(operator.PrivateDeletionOperatorConfigurationError):
        operator.PrivateDeletionOperatorSettings.from_environment(values)


def test_settings_repr_redacts_all_runtime_handles() -> None:
    settings = operator.PrivateDeletionOperatorSettings.from_environment(environment())

    rendered = repr(settings)
    assert "secret" not in rendered
    assert "amazonaws.com" not in rendered
    assert "synthetic-bearer" not in rendered
    assert "w1.example.test" not in rendered


class FakeSqs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.receive_response: dict[str, Any] = {}

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("receive", kwargs))
        return self.receive_response

    def delete_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete", kwargs))
        return {}

    def get_queue_attributes(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("attributes", kwargs))
        queue_name = kwargs["QueueUrl"].rsplit("/", 1)[-1]
        prefix = "arn:aws:sqs:ap-northeast-2:123456789012:"
        return {
            "Attributes": {
                "QueueArn": prefix + queue_name,
                "SqsManagedSseEnabled": "true",
                "RedrivePolicy": json.dumps(
                    {
                        "deadLetterTargetArn": prefix + queue_name + "-dlq",
                        "maxReceiveCount": "5",
                    }
                ),
            }
        }


def test_sqs_queue_receives_at_most_one_with_bounded_long_poll_and_visibility() -> None:
    sqs = FakeSqs()
    sqs.receive_response = {
        "Messages": [
            {
                "Body": '{"schema_version":"synthetic"}',
                "ReceiptHandle": "synthetic-receipt",
                "Attributes": {"SenderId": "AROASYNTHETICW1ROLE12:worker"},
            }
        ]
    }
    settings = operator.PrivateDeletionOperatorSettings.from_environment(environment())
    queue = operator.SqsPrivateDeletionQueue(sqs, settings)

    deliveries = queue.receive()
    queue.delete(deliveries[0].receipt_handle)

    assert deliveries[0].sender_id == "AROASYNTHETICW1ROLE12:worker"
    assert sqs.calls == [
        (
            "receive",
            {
                "QueueUrl": settings.queue_url,
                "MaxNumberOfMessages": 1,
                "WaitTimeSeconds": 10,
                "VisibilityTimeout": 60,
                "MessageSystemAttributeNames": ["SenderId"],
            },
        ),
        (
            "delete",
            {"QueueUrl": settings.queue_url, "ReceiptHandle": "synthetic-receipt"},
        ),
    ]


def test_sqs_queue_errors_are_sanitized() -> None:
    class BrokenSqs(FakeSqs):
        def receive_message(self, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            raise RuntimeError("PRIVATE-CANARY")

    queue = operator.SqsPrivateDeletionQueue(
        BrokenSqs(), operator.PrivateDeletionOperatorSettings.from_environment(environment())
    )

    with pytest.raises(RuntimeError) as caught:
        queue.receive()

    assert "PRIVATE-CANARY" not in str(caught.value)
    assert caught.value.__suppress_context__


def _engine_with_revisions(*revisions: str) -> MagicMock:
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.scalars.return_value.all.return_value = list(revisions)
    return engine


def test_preflight_requires_exact_head_before_creating_sqs_client() -> None:
    engine = _engine_with_revisions("0012_private_ack_wire_digest")
    created: list[object] = []

    with pytest.raises(operator.PrivateDeletionOperatorConfigurationError):
        operator.preflight(
            engine,
            operator.PrivateDeletionOperatorSettings.from_environment(environment()),
            sqs_client_factory=lambda _settings: created.append(object()) or FakeSqs(),
            ack_client_factory=lambda _settings: MagicMock(),
        )

    assert created == []


def test_preflight_accepts_only_exact_head_and_valid_encrypted_dlq_metadata() -> None:
    engine = _engine_with_revisions("0013_deletion_ack_confirmed")
    sqs = FakeSqs()
    callback = MagicMock()

    returned_sqs, returned_callback = operator.preflight(
        engine,
        operator.PrivateDeletionOperatorSettings.from_environment(environment()),
        sqs_client_factory=lambda _settings: sqs,
        ack_client_factory=lambda _settings: callback,
    )

    assert returned_sqs is sqs
    assert returned_callback is callback
    assert [name for name, _kwargs in sqs.calls] == ["attributes"]


@pytest.mark.parametrize("missing", ("QueueArn", "RedrivePolicy", "SqsManagedSseEnabled"))
def test_preflight_rejects_invalid_queue_or_dlq_metadata(missing: str) -> None:
    class BadMetadataSqs(FakeSqs):
        def get_queue_attributes(self, **kwargs: Any) -> dict[str, Any]:
            response = super().get_queue_attributes(**kwargs)
            response["Attributes"].pop(missing)
            return response

    with pytest.raises(operator.PrivateDeletionOperatorConfigurationError):
        operator.preflight(
            _engine_with_revisions("0013_deletion_ack_confirmed"),
            operator.PrivateDeletionOperatorSettings.from_environment(environment()),
            sqs_client_factory=lambda _settings: BadMetadataSqs(),
            ack_client_factory=lambda _settings: MagicMock(),
        )


def test_preflight_rejects_unloadable_ca_without_receiving_a_message(tmp_path: Path) -> None:
    values = environment()
    values["W1_PRIVATE_DELETION_ACK_CA_FILE"] = str(tmp_path / "missing-ca.pem")
    sqs = FakeSqs()

    with pytest.raises(operator.PrivateDeletionOperatorConfigurationError):
        operator.preflight(
            _engine_with_revisions("0013_deletion_ack_confirmed"),
            operator.PrivateDeletionOperatorSettings.from_environment(values),
            sqs_client_factory=lambda _settings: sqs,
        )

    assert [name for name, _kwargs in sqs.calls] == ["attributes"]


def _dispatch_body() -> str:
    deletion_id = "00000000-0000-0000-0000-000000000001"
    owner_id = "00000000-0000-0000-0000-000000000002"
    scope: Mapping[str, object] = {"type": "ACCOUNT"}
    payload = {
        "schema_version": "w2.private-deletion.v2",
        "deletion_id": deletion_id,
        "owner_user_id": owner_id,
        "deletion_epoch": 3,
        "scope": scope,
    }
    return json.dumps(
        {
            "schema_version": "w1.private.w2-deletion-dispatch.v2",
            "message_type": "w1.private.w2.deletion-command.v2",
            "message_id": deletion_id,
            "producer": "w1",
            "occurred_at": datetime(2026, 9, 27, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
            "visibility_scope": "PRIVATE",
            "deletion_request_id": "00000000-0000-0000-0000-000000000003",
            "deletion_target_id": deletion_id,
            "owner_user_id": owner_id,
            "deletion_epoch": 3,
            "scope": scope,
            "payload_schema_version": "w2.private-deletion.v2",
            "payload": payload,
        }
    )


def test_consume_once_wires_db_then_ack_processor_and_deletes_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sqs = FakeSqs()
    sqs.receive_response = {
        "Messages": [
            {
                "Body": _dispatch_body(),
                "ReceiptHandle": "synthetic-receipt",
                "Attributes": {"SenderId": "AROASYNTHETICW1ROLE12:worker"},
            }
        ]
    }
    settings = operator.PrivateDeletionOperatorSettings.from_environment(environment())
    queue = operator.SqsPrivateDeletionQueue(sqs, settings)
    sessions = object()
    callback = object()
    observed: list[tuple[object, object]] = []

    def process(session_factory: object, command: object, actual_callback: object) -> object:
        observed.append((session_factory, actual_callback))
        return PrivateDeletionAckV2(
            deletion_id=UUID("00000000-0000-0000-0000-000000000001"),
            owner_user_id=UUID("00000000-0000-0000-0000-000000000002"),
            deletion_epoch=3,
            scope=command.scope,  # type: ignore[attr-defined]
            outcome="APPLIED",
        )

    monkeypatch.setattr(operator, "process_private_deletion_v2", process)

    result = operator.consume_once(sessions, queue, settings.expected_sender_id, callback)

    assert result.status == "APPLIED"
    assert observed == [(sessions, callback)]
    assert [name for name, _kwargs in sqs.calls] == ["receive", "delete"]


def test_run_loop_finishes_inflight_item_then_observes_stop(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop = Event()
    calls: list[str] = []

    def consume(*_args: object) -> ConsumeResult:
        calls.append("consumed")
        stop.set()
        return ConsumeResult(status="APPLIED")

    exit_code = operator.run_loop(
        object(),
        object(),
        "AROASYNTHETICW1ROLE12",
        object(),
        stop,
        consume=consume,
    )

    assert exit_code == 0
    assert calls == ["consumed"]
    assert json.loads(capsys.readouterr().out) == {"status": "APPLIED"}


def test_inspect_counts_visible_inflight_messages_and_unconfirmed_receipts() -> None:
    class CountSqs(FakeSqs):
        def get_queue_attributes(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["QueueUrl"].endswith("-dlq"):
                self.calls.append(("attributes", kwargs))
                return {
                    "Attributes": {
                        "QueueArn": "arn:aws:sqs:ap-northeast-2:123456789012:"
                        "w1-private-deletion-v2-dlq",
                        "SqsManagedSseEnabled": "true",
                    }
                }
            response = super().get_queue_attributes(**kwargs)
            response["Attributes"].update(
                {
                    "ApproximateNumberOfMessages": "3",
                    "ApproximateNumberOfMessagesNotVisible": "2",
                }
            )
            return response

    engine = _engine_with_revisions("0013_deletion_ack_confirmed")
    engine.connect.return_value.__enter__.return_value.scalar.return_value = 4
    settings = operator.PrivateDeletionOperatorSettings.from_environment(environment())

    counts = operator.inspect_counts(engine, CountSqs(), settings)

    assert counts == {"pending_deletion_count": 5, "pending_ack_count": 4}


def test_inspect_rejects_unencrypted_dlq() -> None:
    class UnencryptedDlqSqs(FakeSqs):
        def get_queue_attributes(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["QueueUrl"].endswith("-dlq"):
                return {
                    "Attributes": {
                        "QueueArn": "arn:aws:sqs:ap-northeast-2:123456789012:"
                        "w1-private-deletion-v2-dlq"
                    }
                }
            response = super().get_queue_attributes(**kwargs)
            response["Attributes"].update(
                {
                    "ApproximateNumberOfMessages": "0",
                    "ApproximateNumberOfMessagesNotVisible": "0",
                }
            )
            return response

    engine = _engine_with_revisions("0013_deletion_ack_confirmed")
    engine.connect.return_value.__enter__.return_value.scalar.return_value = 0
    with pytest.raises(operator.PrivateDeletionOperatorConfigurationError):
        operator.inspect_counts(
            engine,
            UnencryptedDlqSqs(),
            operator.PrivateDeletionOperatorSettings.from_environment(environment()),
        )
