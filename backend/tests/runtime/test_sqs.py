from __future__ import annotations

from typing import Any

import pytest

from app.runtime.sqs import Boto3SqsPort, SqsFinalDeliveryError, SqsRetryableError


class StubBoto3Client:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or {"MessageId": "broker-message-id"}
        self.error = error
        self.calls: list[dict[str, object]] = []

    def send_message(self, **kwargs: object) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class StubClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def test_boto3_sqs_adapter_sends_standard_message_attributes() -> None:
    client = StubBoto3Client()
    port = Boto3SqsPort(client=client)

    broker_id = port.send_message(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w1-execution",
        body='{"command_id":"00000000-0000-4000-8000-000000000001"}',
        message_attributes={"epick_command_id": "00000000-0000-4000-8000-000000000001"},
    )

    assert broker_id == "broker-message-id"
    assert client.calls == [
        {
            "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123/w1-execution",
            "MessageBody": '{"command_id":"00000000-0000-4000-8000-000000000001"}',
            "MessageAttributes": {
                "epick_command_id": {
                    "DataType": "String",
                    "StringValue": "00000000-0000-4000-8000-000000000001",
                }
            },
        }
    ]


@pytest.mark.parametrize(
    ("aws_code", "expected_type"),
    [
        ("ThrottlingException", SqsRetryableError),
        ("AWS.SimpleQueueService.NonExistentQueue", SqsFinalDeliveryError),
    ],
)
def test_boto3_sqs_adapter_classifies_aws_errors(
    aws_code: str,
    expected_type: type[Exception],
) -> None:
    port = Boto3SqsPort(client=StubBoto3Client(error=StubClientError(aws_code)))

    with pytest.raises(expected_type):
        port.send_message(
            queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w1-execution",
            body="{}",
            message_attributes={},
        )
