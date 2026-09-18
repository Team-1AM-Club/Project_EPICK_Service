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

    def get_queue_attributes(self, **kwargs: object) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response

    def receive_message(self, **kwargs: object) -> dict[str, Any]:
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


def test_boto3_sqs_adapter_reads_requested_queue_attributes() -> None:
    client = StubBoto3Client(response={"Attributes": {"QueueArn": "arn:queue"}})
    port = Boto3SqsPort(client=client)

    result = port.get_queue_attributes(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w3-decision",
        attribute_names=("QueueArn", "RedrivePolicy"),
    )

    assert result == {"QueueArn": "arn:queue"}
    assert client.calls == [
        {
            "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123/w3-decision",
            "AttributeNames": ["QueueArn", "RedrivePolicy"],
        }
    ]


def test_boto3_sqs_adapter_reads_authenticated_sender_and_fixed_poll_bounds() -> None:
    client = StubBoto3Client(
        response={
            "Messages": [
                {
                    "MessageId": "message-1",
                    "ReceiptHandle": "receipt-1",
                    "Body": "{}",
                    "Attributes": {
                        "ApproximateReceiveCount": "2",
                        "SenderId": "AROAW3PRODUCER:relay-session",
                    },
                    "MessageAttributes": {},
                }
            ]
        }
    )
    port = Boto3SqsPort(client=client)

    messages = port.receive_messages(
        queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w3-decision",
        max_messages=10,
        visibility_timeout_seconds=120,
        wait_time_seconds=20,
    )

    assert messages[0].sender_id == "AROAW3PRODUCER:relay-session"
    assert messages[0].receive_count == 2
    assert client.calls == [
        {
            "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123/w3-decision",
            "MaxNumberOfMessages": 10,
            "VisibilityTimeout": 120,
            "WaitTimeSeconds": 20,
            "MessageSystemAttributeNames": ["ApproximateReceiveCount", "SenderId"],
            "MessageAttributeNames": ["All"],
        }
    ]
