"""Dedicated fail-closed local operator for W1 private-deletion v2 deliveries."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import signal
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from sqlalchemy import Engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.commit_gate_runtime import ConsumeResult
from epick_engine.source_collection.persistence import (
    PrivateDeletionReceipt,
    create_database_engine,
    create_session_factory,
)
from epick_engine.source_collection.private_deletion_consumer_v2 import (
    PrivateDeletionDeliveryV2,
    PrivateDeletionQueueV2,
    consume_private_deletion_once,
)
from epick_engine.source_collection.private_deletion_v2 import (
    PrivateDeletionAckCallbackV2,
    PrivateDeletionAckV2,
    PrivateDeletionCommandV2,
    process_private_deletion_v2,
)
from epick_engine.source_collection.w1_lookup_client import (
    W1LookupClientError,
    _validate_bearer,
)
from epick_engine.source_collection.w1_private_deletion_ack_client import (
    W1PrivateDeletionAckClient,
    validate_private_deletion_ack_endpoint,
)

_MIGRATION_HEAD = "0013_deletion_ack_confirmed"
_ROLE_ID = re.compile(r"AROA[A-Z0-9]{17}")
_REGION = re.compile(r"[a-z]{2}-[a-z0-9-]+-[0-9]+")
_NONCOMMERCIAL_REGION_PREFIXES = ("cn-", "us-gov-", "us-iso", "eu-iso")
_QUEUE_PATH = re.compile(r"/([0-9]{12})/([A-Za-z0-9_-]{1,80})")
_QUEUE_ARN = re.compile(
    r"arn:(?P<partition>aws(?:-us-gov|-cn)?):sqs:(?P<region>[a-z0-9-]+):"
    r"(?P<account>[0-9]{12}):(?P<name>[A-Za-z0-9_-]+)"
)
_STATIC_AWS_CONFIGURATION = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "AWS_CREDENTIAL_FILE",
        "BOTO_CONFIG",
    }
)
_WORKLOAD_ROLE_PROVIDER_METHODS = frozenset(
    {"assume-role-with-web-identity", "container-role", "iam-role"}
)


class PrivateDeletionOperatorConfigurationError(ValueError):
    """Sanitized configuration failure that never includes a supplied value."""


@dataclass(frozen=True, repr=False)
class PrivateDeletionOperatorSettings:
    """Required deployment handles; repr intentionally exposes none of them."""

    database_url: str = field(repr=False)
    region: str = field(repr=False)
    queue_url: str = field(repr=False)
    ack_endpoint: str = field(repr=False)
    ack_bearer: str = field(repr=False)
    ack_ca_file: str = field(repr=False)
    expected_sender_id: str = field(repr=False)

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> PrivateDeletionOperatorSettings:
        """Require an explicit production configuration and reject static AWS selectors."""

        try:
            required = {
                name: values[name]
                for name in (
                    "EPICK_DATABASE_URL",
                    "W2_PRIVATE_DELETION_REGION",
                    "W1_PRIVATE_DELETION_COMMAND_QUEUE_URL",
                    "W1_PRIVATE_DELETION_ACK_ENDPOINT",
                    "W1_PRIVATE_DELETION_ACK_BEARER",
                    "W1_PRIVATE_DELETION_ACK_CA_FILE",
                    "W1_EXPECTED_SYSTEM_SENDER_ID",
                )
            }
            if any(not isinstance(value, str) or not value.strip() for value in required.values()):
                raise ValueError
            if any(name in values for name in _STATIC_AWS_CONFIGURATION):
                raise ValueError

            database_url = required["EPICK_DATABASE_URL"]
            if make_url(database_url).drivername != "postgresql+psycopg":
                raise ValueError

            region = required["W2_PRIVATE_DELETION_REGION"]
            if _REGION.fullmatch(region) is None or region.startswith(
                _NONCOMMERCIAL_REGION_PREFIXES
            ):
                raise ValueError
            queue_url = required["W1_PRIVATE_DELETION_COMMAND_QUEUE_URL"]
            parsed_queue = urlsplit(queue_url)
            if (
                parsed_queue.scheme != "https"
                or parsed_queue.netloc != f"sqs.{region}.amazonaws.com"
                or parsed_queue.query
                or parsed_queue.fragment
                or _QUEUE_PATH.fullmatch(parsed_queue.path) is None
            ):
                raise ValueError

            ack_endpoint = required["W1_PRIVATE_DELETION_ACK_ENDPOINT"]
            validate_private_deletion_ack_endpoint(ack_endpoint)
            ack_bearer = _validate_bearer(required["W1_PRIVATE_DELETION_ACK_BEARER"])
            expected_sender_id = required["W1_EXPECTED_SYSTEM_SENDER_ID"]
            if _ROLE_ID.fullmatch(expected_sender_id) is None:
                raise ValueError

            return cls(
                database_url=database_url,
                region=region,
                queue_url=queue_url,
                ack_endpoint=ack_endpoint,
                ack_bearer=ack_bearer,
                ack_ca_file=required["W1_PRIVATE_DELETION_ACK_CA_FILE"],
                expected_sender_id=expected_sender_id,
            )
        except (KeyError, TypeError, ValueError, W1LookupClientError):
            raise PrivateDeletionOperatorConfigurationError(
                "private deletion operator configuration is invalid"
            ) from None


class SqsClient(Protocol):
    def receive_message(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete_message(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_queue_attributes(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SqsPrivateDeletionDelivery:
    receipt_handle: str
    body: str
    sender_id: str | None


class SqsPrivateDeletionQueue:
    """Bounded SQS adapter that exposes only authenticated system sender metadata."""

    def __init__(self, client: SqsClient, settings: PrivateDeletionOperatorSettings) -> None:
        self._client = client
        self._queue_url = settings.queue_url

    def receive(self) -> Sequence[PrivateDeletionDeliveryV2]:
        try:
            response = self._client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=10,
                VisibilityTimeout=60,
                MessageSystemAttributeNames=["SenderId"],
            )
            messages = response.get("Messages", [])
            if not isinstance(messages, list) or len(messages) > 1:
                raise ValueError
            deliveries: list[SqsPrivateDeletionDelivery] = []
            for item in messages:
                if not isinstance(item, Mapping):
                    raise ValueError
                body = item.get("Body")
                receipt_handle = item.get("ReceiptHandle")
                attributes = item.get("Attributes", {})
                if (
                    not isinstance(body, str)
                    or not isinstance(receipt_handle, str)
                    or not receipt_handle
                    or not isinstance(attributes, Mapping)
                ):
                    raise ValueError
                sender_id = attributes.get("SenderId")
                deliveries.append(
                    SqsPrivateDeletionDelivery(
                        receipt_handle=receipt_handle,
                        body=body,
                        sender_id=sender_id if isinstance(sender_id, str) else None,
                    )
                )
            return deliveries
        except Exception:
            raise RuntimeError("private deletion queue receive failed") from None

    def delete(self, receipt_handle: str) -> None:
        try:
            if not isinstance(receipt_handle, str) or not receipt_handle:
                raise ValueError
            self._client.delete_message(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
            )
        except Exception:
            raise RuntimeError("private deletion queue delete failed") from None


def create_sqs_client(settings: PrivateDeletionOperatorSettings) -> SqsClient:
    """Build an SQS client from workload-role credential providers only."""

    try:
        if any(name in os.environ for name in _STATIC_AWS_CONFIGURATION):
            raise ValueError
        boto3 = importlib.import_module("boto3")
        botocore_session = importlib.import_module("botocore.session").get_session()
        resolver = botocore_session.get_component("credential_provider")
        providers = [
            provider
            for provider in resolver.providers
            if getattr(provider, "METHOD", None) in _WORKLOAD_ROLE_PROVIDER_METHODS
        ]
        if not providers:
            raise ValueError
        resolver.providers = providers
        session = boto3.Session(botocore_session=botocore_session)
        config = importlib.import_module("botocore.config").Config(
            connect_timeout=5,
            read_timeout=15,
            retries={"mode": "standard", "total_max_attempts": 1},
            ignore_configured_endpoint_urls=True,
        )
        return cast(SqsClient, session.client("sqs", region_name=settings.region, config=config))
    except Exception:
        raise PrivateDeletionOperatorConfigurationError(
            "private deletion AWS workload identity is invalid"
        ) from None


def create_ack_client(settings: PrivateDeletionOperatorSettings) -> W1PrivateDeletionAckClient:
    """Create the exact W1 atomic purge-and-ACK callback client."""

    try:
        ssl_context = ssl.create_default_context(cafile=settings.ack_ca_file)
        return W1PrivateDeletionAckClient(
            endpoint=settings.ack_endpoint,
            bearer=settings.ack_bearer,
            ssl_context=ssl_context,
        )
    except Exception:
        raise PrivateDeletionOperatorConfigurationError(
            "private deletion W1 acknowledgement configuration is invalid"
        ) from None


def _validate_queue_metadata(client: SqsClient, settings: PrivateDeletionOperatorSettings) -> str:
    try:
        attributes = client.get_queue_attributes(
            QueueUrl=settings.queue_url,
            AttributeNames=["QueueArn", "RedrivePolicy", "SqsManagedSseEnabled", "KmsMasterKeyId"],
        ).get("Attributes", {})
        if not isinstance(attributes, Mapping):
            raise ValueError

        path_match = _QUEUE_PATH.fullmatch(urlsplit(settings.queue_url).path)
        if path_match is None:
            raise ValueError
        account, queue_name = path_match.groups()
        expected_arn = f"arn:aws:sqs:{settings.region}:{account}:{queue_name}"
        if attributes.get("QueueArn") != expected_arn:
            raise ValueError

        redrive = json.loads(attributes.get("RedrivePolicy", ""))
        if not isinstance(redrive, Mapping):
            raise ValueError
        dlq_arn = redrive.get("deadLetterTargetArn")
        dlq_match = _QUEUE_ARN.fullmatch(dlq_arn) if isinstance(dlq_arn, str) else None
        max_receive_count = redrive.get("maxReceiveCount")
        kms_key = attributes.get("KmsMasterKeyId")
        if (
            dlq_match is None
            or dlq_arn == expected_arn
            or dlq_match.group("partition") != "aws"
            or dlq_match.group("region") != settings.region
            or dlq_match.group("account") != account
            or isinstance(max_receive_count, bool)
            or not isinstance(max_receive_count, str | int)
            or not 1 <= int(max_receive_count) <= 1000
            or not (
                attributes.get("SqsManagedSseEnabled") == "true"
                or isinstance(kms_key, str)
                and bool(kms_key.strip())
            )
        ):
            raise ValueError
        assert isinstance(dlq_arn, str)
        return dlq_arn
    except Exception:
        raise PrivateDeletionOperatorConfigurationError(
            "private deletion queue metadata is invalid"
        ) from None


def count_pending_private_deletion_acks(engine: Engine) -> int:
    """Count v2 receipts without a durable W1 200 ACK confirmation."""

    try:
        with engine.connect() as connection:
            count = connection.scalar(
                select(func.count())
                .select_from(PrivateDeletionReceipt)
                .where(
                    PrivateDeletionReceipt.contract_version == "w2.private-deletion.v2",
                    PrivateDeletionReceipt.ack_confirmed_at.is_(None),
                )
            )
            if not isinstance(count, int) or count < 0:
                raise ValueError
            return count
    except Exception:
        raise PrivateDeletionOperatorConfigurationError(
            "private deletion ACK count is unavailable"
        ) from None


def inspect_counts(
    engine: Engine,
    client: SqsClient,
    settings: PrivateDeletionOperatorSettings,
) -> dict[str, int]:
    """Return count-only queue backlog and durable unconfirmed W1 ACKs."""

    try:
        dlq_arn = _validate_queue_metadata(client, settings)
        attributes = client.get_queue_attributes(
            QueueUrl=settings.queue_url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        ).get("Attributes", {})
        if not isinstance(attributes, Mapping):
            raise ValueError
        counts: list[int] = []
        for name in (
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ):
            value = attributes.get(name)
            if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
                raise ValueError
            counts.append(int(value))

        dlq_match = _QUEUE_ARN.fullmatch(dlq_arn)
        if dlq_match is None:
            raise ValueError
        dlq_url = (
            f"https://sqs.{settings.region}.amazonaws.com/"
            f"{dlq_match.group('account')}/{dlq_match.group('name')}"
        )
        dlq_attributes = client.get_queue_attributes(
            QueueUrl=dlq_url,
            AttributeNames=["QueueArn", "SqsManagedSseEnabled", "KmsMasterKeyId"],
        ).get("Attributes", {})
        if (
            not isinstance(dlq_attributes, Mapping)
            or dlq_attributes.get("QueueArn") != dlq_arn
            or not (
                dlq_attributes.get("SqsManagedSseEnabled") == "true"
                or isinstance(dlq_attributes.get("KmsMasterKeyId"), str)
                and bool(dlq_attributes["KmsMasterKeyId"].strip())
            )
        ):
            raise ValueError
        return {
            "pending_deletion_count": sum(counts),
            "pending_ack_count": count_pending_private_deletion_acks(engine),
        }
    except Exception:
        raise PrivateDeletionOperatorConfigurationError(
            "private deletion inspect is unavailable"
        ) from None


SqsClientFactory = Callable[[PrivateDeletionOperatorSettings], SqsClient]
AckClientFactory = Callable[
    [PrivateDeletionOperatorSettings],
    PrivateDeletionAckCallbackV2,
]


def preflight(
    engine: Engine,
    settings: PrivateDeletionOperatorSettings,
    *,
    sqs_client_factory: SqsClientFactory | None = None,
    ack_client_factory: AckClientFactory | None = None,
) -> tuple[SqsClient, PrivateDeletionAckCallbackV2]:
    """Validate DB, queue metadata, and local TLS material without message I/O."""

    try:
        with engine.connect() as connection:
            revisions = tuple(
                connection.scalars(text("SELECT version_num FROM alembic_version")).all()
            )
            if revisions != (_MIGRATION_HEAD,):
                raise ValueError
    except Exception:
        raise PrivateDeletionOperatorConfigurationError(
            "private deletion database migration is invalid"
        ) from None

    factory = create_sqs_client if sqs_client_factory is None else sqs_client_factory
    client = factory(settings)
    _validate_queue_metadata(client, settings)
    callback_factory = create_ack_client if ack_client_factory is None else ack_client_factory
    callback = callback_factory(settings)
    return client, callback


def consume_once(
    session_factory: sessionmaker[Session],
    queue: PrivateDeletionQueueV2,
    expected_sender_id: str,
    callback: PrivateDeletionAckCallbackV2,
) -> ConsumeResult:
    """Process one command through the commit-before-ACK v2 boundary."""

    def processor(command: PrivateDeletionCommandV2) -> PrivateDeletionAckV2 | None:
        return process_private_deletion_v2(session_factory, command, callback)

    return consume_private_deletion_once(queue, expected_sender_id, processor)


class ConsumeOperation(Protocol):
    def __call__(
        self,
        session_factory: sessionmaker[Session],
        queue: PrivateDeletionQueueV2,
        expected_sender_id: str,
        callback: PrivateDeletionAckCallbackV2,
    ) -> ConsumeResult: ...


def run_loop(
    session_factory: sessionmaker[Session],
    queue: PrivateDeletionQueueV2,
    expected_sender_id: str,
    callback: PrivateDeletionAckCallbackV2,
    stop: Event,
    *,
    consume: ConsumeOperation = consume_once,
) -> int:
    """Finish an in-flight bounded delivery after SIGTERM, then stop receiving."""

    while not stop.is_set():
        result = consume(session_factory, queue, expected_sender_id, callback)
        if result.status != "EMPTY":
            print(json.dumps({"status": result.status}, sort_keys=True), flush=True)
        if result.status in {"RECEIVE_FAILED", "DELETE_FAILED"}:
            return 1
        if result.status != "APPLIED":
            stop.wait(1)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run preflight, one bounded delivery, or the SIGTERM-aware local worker."""

    parser = argparse.ArgumentParser(description="W2 private-deletion v2 operator")
    parser.add_argument("action", choices=("preflight", "inspect", "consume-once", "run"))
    args = parser.parse_args(argv)
    engine: Engine | None = None
    try:
        settings = PrivateDeletionOperatorSettings.from_environment(os.environ)
        engine = create_database_engine(settings.database_url)
        sqs_client, callback = preflight(engine, settings)
        if args.action == "preflight":
            print(json.dumps({"status": "PREFLIGHT_PASSED", "scope": "metadata_only"}))
            return 0
        if args.action == "inspect":
            print(json.dumps(inspect_counts(engine, sqs_client, settings), sort_keys=True))
            return 0

        session_factory = create_session_factory(engine)
        queue = SqsPrivateDeletionQueue(sqs_client, settings)
        if args.action == "consume-once":
            result = consume_once(
                session_factory,
                queue,
                settings.expected_sender_id,
                callback,
            )
            print(json.dumps({"status": result.status}, sort_keys=True))
            return 0 if result.status in {"APPLIED", "EMPTY"} else 1

        stop = Event()
        previous = {
            signum: signal.signal(signum, lambda _signum, _frame: stop.set())
            for signum in (signal.SIGTERM, signal.SIGINT)
        }
        try:
            return run_loop(
                session_factory,
                queue,
                settings.expected_sender_id,
                callback,
                stop,
            )
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    except Exception:
        print('{"status":"PRIVATE_DELETION_FAILED","details":"consult approved operator checks"}')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
