"""Explicit CT15-only operator surface; deployment and IAM are owned by W1."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import signal
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.commit_gate_runtime import (
    MAX_OUTBOUND_MESSAGE_BYTES,
    PrivateWriteAuthorityProvider,
    Queue,
    QueueDelivery,
    RelayAuthorityClient,
    SessionFactory,
    _strict_json_object,
    _wire_body,
    consume_once,
    relay_once,
)
from epick_engine.source_collection.commit_gate_store import (
    PrivateCommitGateAck,
    PrivateCommitStage,
    PrivateStagedOutbox,
    _lock_command,
    stage_private_result,
)
from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult
from epick_engine.source_collection.ct15_inspection import inspect_run_counts, load_run_scope
from epick_engine.source_collection.private_scope import (
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
)
from epick_engine.source_collection.w1_lookup_client import (
    W1LookupClientError,
    validate_lookup_endpoint,
)
from epick_engine.source_collection.w1_private_authority_client import W1PrivateAuthorityClient
from epick_engine.source_collection.w1_transport import _parse_wire

_ROLE_ID = re.compile(r"AROA[A-Z0-9]{17}")
_STATES = ("STAGED", "PREPARED", "FINALIZED", "ABORTED", "PURGED")
_MIGRATION_HEAD = "0013_deletion_ack_confirmed"


class Ct15ConfigurationError(ValueError):
    """Fixed diagnostic that never interpolates operator configuration."""


def _isolated_name(value: str) -> bool:
    return "ct15" in re.split(r"[-_]", value.lower())


@dataclass(frozen=True, repr=False)
class Ct15Settings:
    database_url: str = field(repr=False)
    region: str
    command_queue_url: str = field(repr=False)
    inbound_queue_url: str = field(repr=False)
    expected_w1_sender_id: str
    runtime_label: str
    lookup_endpoint: str = field(repr=False)
    lookup_bearer: str = field(repr=False)
    lookup_ca_file: str = field(repr=False)

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> Ct15Settings:
        try:
            lookup_values = {
                name: values[name]
                for name in (
                    "W1_LOOKUP_ENDPOINT",
                    "W1_LOOKUP_BEARER",
                    "W1_LOOKUP_CA_FILE",
                )
            }
            if any(
                not isinstance(value, str) or not value.strip() for value in lookup_values.values()
            ):
                raise ValueError
            validate_lookup_endpoint(lookup_values["W1_LOOKUP_ENDPOINT"])
            if (
                values["W2_CT15_ENABLED"] != "true"
                or values["W2_CT15_GATE_ONLY_QUEUE_APPROVED"] != "true"
            ):
                raise ValueError
            settings = cls(
                database_url=values["W2_CT15_DATABASE_URL"],
                region=values["W2_CT15_REGION"],
                command_queue_url=values["W2_CT15_COMMAND_QUEUE_URL"],
                inbound_queue_url=values["W2_CT15_INBOUND_QUEUE_URL"],
                expected_w1_sender_id=values["W2_CT15_EXPECTED_W1_SENDER_ID"],
                runtime_label=values["W2_CT15_RUNTIME_LABEL"],
                lookup_endpoint=lookup_values["W1_LOOKUP_ENDPOINT"],
                lookup_bearer=lookup_values["W1_LOOKUP_BEARER"],
                lookup_ca_file=lookup_values["W1_LOOKUP_CA_FILE"],
            )
            db = make_url(settings.database_url)
            if db.drivername != "postgresql+psycopg" or not _isolated_name(db.database or ""):
                raise ValueError
            if not _isolated_name(settings.runtime_label):
                raise ValueError
            if not _ROLE_ID.fullmatch(settings.expected_w1_sender_id):
                raise ValueError
            if not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]+", settings.region):
                raise ValueError
            for queue in (settings.command_queue_url, settings.inbound_queue_url):
                parts = urlsplit(queue)
                match = re.fullmatch(r"/([0-9]{12})/([A-Za-z0-9_-]{1,80})", parts.path)
                if (
                    parts.scheme != "https"
                    or parts.netloc != f"sqs.{settings.region}.amazonaws.com"
                    or parts.query
                    or parts.fragment
                    or match is None
                    or not _isolated_name(match.group(2))
                ):
                    raise ValueError
            if settings.command_queue_url == settings.inbound_queue_url:
                raise ValueError
            return settings
        except (KeyError, TypeError, ValueError, W1LookupClientError):
            raise Ct15ConfigurationError("explicit isolated CT15 settings required") from None


class SqsClient(Protocol):
    def receive_message(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete_message(self, **kwargs: Any) -> dict[str, Any]: ...

    def send_message(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_queue_attributes(self, **kwargs: Any) -> dict[str, Any]: ...


class SqsGateQueue:
    """Only system attributes authenticate a delivery; message attributes do not."""

    def __init__(self, client: SqsClient, settings: Ct15Settings) -> None:
        self.client = client
        self.settings = settings

    def receive(self) -> Sequence[QueueDelivery]:
        try:
            response = self.client.receive_message(
                QueueUrl=self.settings.command_queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=10,
                VisibilityTimeout=60,
                MessageSystemAttributeNames=["SenderId"],
            )
            messages = response.get("Messages", [])
            if not isinstance(messages, list) or len(messages) > 1:
                raise ValueError
            deliveries = []
            for item in messages:
                body, receipt = item.get("Body"), item.get("ReceiptHandle")
                sender = item.get("Attributes", {}).get("SenderId")
                if not isinstance(body, str) or not isinstance(receipt, str) or not receipt:
                    raise ValueError
                deliveries.append(
                    QueueDelivery(receipt, body, sender if isinstance(sender, str) else None)
                )
            return deliveries
        except Exception:
            raise RuntimeError("SQS receive unavailable") from None

    def delete(self, receipt_handle: str) -> None:
        try:
            self.client.delete_message(
                QueueUrl=self.settings.command_queue_url, ReceiptHandle=receipt_handle
            )
        except Exception:
            raise RuntimeError("SQS receipt delete unconfirmed") from None

    def send(self, body: str) -> None:
        try:
            response = self.client.send_message(
                QueueUrl=self.settings.inbound_queue_url, MessageBody=body
            )
            expected = hashlib.md5(body.encode("utf-8"), usedforsecurity=False).hexdigest()
            if not response.get("MessageId") or response.get("MD5OfMessageBody") != expected:
                raise ValueError
        except Exception:
            raise RuntimeError("SQS delivery unconfirmed") from None


def create_sqs_client(settings: Ct15Settings) -> SqsClient:
    # Optional extra; the host workload role supplies credentials via the SDK chain.
    boto3 = importlib.import_module("boto3")
    config = importlib.import_module("botocore.config").Config(
        connect_timeout=5,
        read_timeout=15,
        retries={"mode": "standard", "total_max_attempts": 1},
        ignore_configured_endpoint_urls=True,
    )
    client: SqsClient = boto3.client("sqs", region_name=settings.region, config=config)
    return client


def create_ct15_engine(settings: Ct15Settings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c lock_timeout=5000 -c statement_timeout=15000",
        },
    )


def create_private_authority_client(settings: Ct15Settings) -> W1PrivateAuthorityClient:
    return W1PrivateAuthorityClient(
        endpoint=settings.lookup_endpoint,
        bearer=settings.lookup_bearer,
        ssl_context=ssl.create_default_context(cafile=settings.lookup_ca_file),
    )


def preflight(engine: Engine, client: SqsClient, settings: Ct15Settings) -> dict[str, str]:
    """Read metadata only; never receive/delete/send a message or migrate a DB."""
    with engine.connect() as connection:
        if not _isolated_name(connection.scalar(text("SELECT current_database()")) or ""):
            raise Ct15ConfigurationError("isolated CT15 database required")
        revisions = tuple(connection.scalars(text("SELECT version_num FROM alembic_version")).all())
        if revisions != (_MIGRATION_HEAD,):
            raise Ct15ConfigurationError("CT15 collection runtime migration required")
        for table in (PrivateStagedOutbox, PrivateCommitGateAck):
            connection.execute(select(table.delivered_at).limit(0))
    for queue_url in (settings.command_queue_url, settings.inbound_queue_url):
        account, queue_name = urlsplit(queue_url).path.strip("/").split("/")
        attrs = client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn", "RedrivePolicy", "SqsManagedSseEnabled", "KmsMasterKeyId"],
        ).get("Attributes", {})
        expected = f"arn:aws:sqs:{settings.region}:{account}:{queue_name}"
        redrive = json.loads(attrs.get("RedrivePolicy", "{}"))
        dlq = redrive.get("deadLetterTargetArn", "")
        if (
            attrs.get("QueueArn") != expected
            or not dlq.startswith(f"arn:aws:sqs:{settings.region}:{account}:")
            or not _isolated_name(dlq.rsplit(":", 1)[-1])
            or dlq == expected
            or not 1 <= int(redrive.get("maxReceiveCount", 0)) <= 1000
            or not (attrs.get("SqsManagedSseEnabled") == "true" or attrs.get("KmsMasterKeyId"))
        ):
            raise Ct15ConfigurationError("isolated encrypted queue with DLQ required")
    return {"status": "PREFLIGHT_PASSED", "scope": "metadata_only_not_permission_or_e2e_proof"}


def inspect_counts(session: Session, *, owner_ref: UUID, command_id: UUID) -> dict[str, object]:
    """Operator-only synthetic scope; never return IDs, hashes or private payloads."""
    predicate = (
        PrivateCommitStage.owner_ref == owner_ref,
        PrivateCommitStage.command_id == command_id,
    )
    _lock_command(session, command_id)
    states: dict[str, int] = {
        state: count
        for state, count in session.execute(
            select(PrivateCommitStage.state, func.count())
            .where(*predicate)
            .group_by(PrivateCommitStage.state)
        ).all()
    }
    row = session.scalar(select(PrivateCommitStage).where(*predicate))
    staged_count = ack_count = pending_staged = pending_ack = staged_payloads = 0
    if row is not None:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command_id)
        )
        if staged is not None:
            staged_count = 1
            staged_payloads = int(staged.payload is not None)
            pending_staged = int(staged.payload is not None and staged.delivered_at is None)
        ack_count = (
            session.scalar(
                select(func.count())
                .select_from(PrivateCommitGateAck)
                .where(PrivateCommitGateAck.command_id == command_id)
            )
            or 0
        )
        pending_ack = (
            session.scalar(
                select(func.count())
                .select_from(PrivateCommitGateAck)
                .where(
                    PrivateCommitGateAck.command_id == command_id,
                    PrivateCommitGateAck.delivered_at.is_(None),
                )
            )
            or 0
        )
    return {
        "schema_version": "w2.ct15.inspection.v1",
        "state_counts": {state: int(states.get(state, 0)) for state in _STATES},
        "result_payload_count": int(row is not None and row.result_payload is not None),
        "visible_result_count": int(row is not None and row.state == "FINALIZED"),
        "staged_outbox_count": staged_count,
        "staged_payload_count": staged_payloads,
        "pending_staged_count": pending_staged,
        "ack_count": ack_count,
        "pending_ack_count": pending_ack,
    }


def stage_synthetic_input(
    sessions: SessionFactory,
    input_path: Path,
    *,
    authority_provider: Callable[[CollectionCommand], PrivateWriteAuthorityDecision] | None = None,
) -> None:
    """Validate the wrapped wire size before committing a synthetic staged result."""
    if input_path.stat().st_size > MAX_OUTBOUND_MESSAGE_BYTES:
        raise Ct15ConfigurationError("synthetic fixture exceeds bound")
    data = input_path.read_bytes()
    raw = _strict_json_object(data.decode("utf-8"), max_bytes=MAX_OUTBOUND_MESSAGE_BYTES)
    command = _parse_wire(raw["command"], CollectionCommand, label="synthetic command")
    result = _parse_wire(raw["result"], CollectionResult, label="synthetic result")
    if authority_provider is None:
        raise Ct15ConfigurationError("trusted private authority provider is required")
    private_scope = PrivateWriteScope(authority_provider(command))
    with sessions.begin() as session:
        proposal = stage_private_result(
            session,
            command,
            result,
            message_id=uuid4(),
            occurred_at=datetime.now(UTC),
            private_scope=private_scope,
        )
        _wire_body(proposal.model_dump(mode="json"))


def run_loop(
    action: str,
    sessions: SessionFactory,
    queue: Queue,
    expected_sender_id: str,
    stop: Event,
    *,
    authority_provider: PrivateWriteAuthorityProvider | None = None,
    private_authority_client: RelayAuthorityClient | None = None,
) -> int:
    """Finish the current bounded transaction on SIGTERM, then stop receiving."""
    if private_authority_client is None:
        return 1
    while not stop.is_set():
        outcome = (
            consume_once(
                sessions,
                queue,
                expected_sender_id,
                authority_provider=authority_provider,
                private_authority_client=private_authority_client,
            )
            if action == "consume"
            else relay_once(sessions, queue, authority_client=private_authority_client)
        )
        if outcome.status != "EMPTY":
            print(json.dumps({"status": outcome.status}), flush=True)
        if outcome.status in {"RECEIVE_FAILED", "SEND_FAILED", "DELETE_FAILED"}:
            return 1
        if outcome.status not in {"APPLIED", "SENT"}:
            stop.wait(1)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W2 isolated CT15 operator")
    parser.add_argument(
        "action",
        choices=[
            "preflight",
            "consume-once",
            "relay-once",
            "consume",
            "relay",
            "inspect",
            "inspect-run",
            "stage",
        ],
    )
    parser.add_argument("--owner-ref", type=UUID)
    parser.add_argument("--command-id", type=UUID)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args(argv)
    engine = None
    try:
        settings = Ct15Settings.from_environment(os.environ)
        engine = create_ct15_engine(settings)
        sessions = sessionmaker(engine, expire_on_commit=False)
        client = create_sqs_client(settings)
        preflight(engine, client, settings)
        private_authority_client = (
            create_private_authority_client(settings)
            if args.action in {"consume-once", "relay-once", "consume", "relay"}
            else None
        )
        result: dict[str, object]
        if args.action == "preflight":
            result = {"status": "PREFLIGHT_PASSED", "scope": "metadata_only"}
        elif args.action == "inspect":
            if args.owner_ref is None or args.command_id is None:
                raise Ct15ConfigurationError("inspection requires explicit synthetic scope")
            with sessions() as session, session.begin():
                result = inspect_counts(
                    session, owner_ref=args.owner_ref, command_id=args.command_id
                )
        elif args.action == "inspect-run":
            if args.input is None:
                raise Ct15ConfigurationError("run inspection requires explicit synthetic scope")
            scope = load_run_scope(args.input)
            with sessions() as session, session.begin():
                result = inspect_run_counts(session, scope)
        elif args.action == "stage":
            if os.environ.get("W2_CT15_SYNTHETIC_INPUTS") != "true" or args.input is None:
                raise Ct15ConfigurationError("explicit synthetic input approval required")
            stage_synthetic_input(sessions, args.input)
            result = {"status": "STAGED_SYNTHETIC"}
        elif args.action in {"consume", "relay"}:
            stop = Event()
            previous = {
                sig: signal.signal(sig, lambda _signal, _frame: stop.set())
                for sig in (signal.SIGTERM, signal.SIGINT)
            }
            try:
                return run_loop(
                    args.action,
                    sessions,
                    SqsGateQueue(client, settings),
                    settings.expected_w1_sender_id,
                    stop,
                    private_authority_client=private_authority_client,
                )
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        else:
            queue = SqsGateQueue(client, settings)
            outcome = (
                consume_once(
                    sessions,
                    queue,
                    settings.expected_w1_sender_id,
                    private_authority_client=private_authority_client,
                )
                if args.action == "consume-once"
                else relay_once(sessions, queue, authority_client=private_authority_client)
            )
            result = {"status": outcome.status}
        print(json.dumps(result, sort_keys=True))
        return (
            0
            if result.get("status")
            in {None, "PREFLIGHT_PASSED", "STAGED_SYNTHETIC", "APPLIED", "SENT", "EMPTY"}
            else 1
        )
    except Exception:
        # Driver/SDK/Pydantic diagnostics may contain credentials, SQL or private data.
        print('{"status":"CT15_FAILED","details":"consult approved operator checks"}')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
