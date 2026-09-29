"""Fail-closed process boundary for the general W2 source runtime.

The durable collection and relay routes intentionally remain injected by the
runtime router.  This module owns only deployment settings, safe preflight,
and the small SQS boundary needed by that router.
"""

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
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from threading import Event
from typing import Any, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cryptography import x509
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from epick_engine.source_collection.collector import StaticScrapyCollector
from epick_engine.source_collection.commit_gate_runtime import relay_once
from epick_engine.source_collection.parsing import extract_static_candidate
from epick_engine.source_collection.persistence import create_session_factory
from epick_engine.source_collection.source_runtime import (
    PrivateAuthorityClient,
    build_collection_relay_authorizer,
    consume_source_runtime_once,
    handle_collection_dispatch,
)
from epick_engine.source_collection.source_runtime_gate import apply_collection_commit_gate
from epick_engine.source_collection.source_runtime_input import (
    RuntimeSourceConfigFile,
    SourceRuntimeInputError,
    SqlAlchemyCollectionInputProvider,
    parse_runtime_source_config_json,
)
from epick_engine.source_collection.w1_lookup_client import (
    W1LookupClient,
    W1LookupClientError,
    validate_lookup_endpoint,
)
from epick_engine.source_collection.w1_private_authority_client import W1PrivateAuthorityClient

_ROLE_ID = re.compile(r"AROA[A-Z0-9]{17}")
_QUEUE_HOST = re.compile(r"^sqs\.([a-z0-9-]+)\.amazonaws\.com$")
_QUEUE_ARN = re.compile(
    r"^arn:(?P<partition>aws(?:-us-gov|-cn)?):sqs:(?P<region>[a-z0-9-]+):"
    r"(?P<account>[0-9]{12}):(?P<name>[A-Za-z0-9_-]+)$"
)
_MIGRATION_HEAD = "0013_deletion_ack_confirmed"
_RUNTIME_CONFIG_PATH = "/run/epick/source-runtime/config.json"
_LOOKUP_CA_PATH = "/run/epick/source-runtime/w1-ca.pem"
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
    {
        "assume-role-with-web-identity",
        "container-role",
        "iam-role",
    }
)


class SourceRuntimeConfigurationError(ValueError):
    """Fixed diagnostic that never interpolates an operator setting."""


class SqsClient(Protocol):
    def receive_message(self, **kwargs: Any) -> dict[str, Any]: ...

    def change_message_visibility(self, **kwargs: Any) -> dict[str, Any]: ...

    def delete_message(self, **kwargs: Any) -> dict[str, Any]: ...

    def send_message(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_queue_attributes(self, **kwargs: Any) -> dict[str, Any]: ...


def _require_workload_role_credentials(values: Mapping[str, str]) -> None:
    """Reject local/static credential selectors before an SDK session exists."""

    if any(name in values for name in _STATIC_AWS_CONFIGURATION):
        raise ValueError


@dataclass(frozen=True, repr=False)
class SourceRuntimeSettings:
    """Non-secret handles are kept private in repr along with secret values."""

    database_url: str = field(repr=False)
    runtime_config_file: str = field(repr=False)
    lookup_endpoint: str = field(repr=False)
    lookup_bearer: str = field(repr=False)
    lookup_ca_file: str = field(repr=False)
    collection_command_queue_url: str = field(repr=False)
    commit_gate_command_queue_url: str = field(repr=False)
    private_inbound_queue_url: str = field(repr=False)
    expected_system_sender_id: str
    region: str

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> SourceRuntimeSettings:
        """Load only syntactically safe settings; filesystem/network checks are preflight."""

        try:
            required = {
                name: values[name]
                for name in (
                    "EPICK_DATABASE_URL",
                    "W2_SOURCE_RUNTIME_CONFIG_FILE",
                    "W1_LOOKUP_ENDPOINT",
                    "W1_LOOKUP_BEARER",
                    "W1_LOOKUP_CA_FILE",
                    "W1_COLLECTION_COMMAND_QUEUE_URL",
                    "W1_COMMIT_GATE_COMMAND_QUEUE_URL",
                    "W1_PRIVATE_INBOUND_QUEUE_URL",
                    "W1_EXPECTED_SYSTEM_SENDER_ID",
                )
            }
            if any(not isinstance(value, str) or not value.strip() for value in required.values()):
                raise ValueError
            _require_workload_role_credentials(values)

            database_url = required["EPICK_DATABASE_URL"]
            db = make_url(database_url)
            if db.drivername != "postgresql+psycopg":
                raise ValueError

            validate_lookup_endpoint(required["W1_LOOKUP_ENDPOINT"])

            queue_urls = (
                required["W1_COLLECTION_COMMAND_QUEUE_URL"],
                required["W1_COMMIT_GATE_COMMAND_QUEUE_URL"],
                required["W1_PRIVATE_INBOUND_QUEUE_URL"],
            )
            url_regions = {_queue_url_region(queue_url) for queue_url in queue_urls}
            known_regions = {region for region in url_regions if region is not None}
            if len(known_regions) != 1:
                raise ValueError
            if not _ROLE_ID.fullmatch(required["W1_EXPECTED_SYSTEM_SENDER_ID"]):
                raise ValueError
            if required["W2_SOURCE_RUNTIME_CONFIG_FILE"] != _RUNTIME_CONFIG_PATH:
                raise ValueError
            if required["W1_LOOKUP_CA_FILE"] != _LOOKUP_CA_PATH:
                raise ValueError

            return cls(
                database_url=database_url,
                runtime_config_file=required["W2_SOURCE_RUNTIME_CONFIG_FILE"],
                lookup_endpoint=required["W1_LOOKUP_ENDPOINT"],
                lookup_bearer=required["W1_LOOKUP_BEARER"],
                lookup_ca_file=required["W1_LOOKUP_CA_FILE"],
                collection_command_queue_url=queue_urls[0],
                commit_gate_command_queue_url=queue_urls[1],
                private_inbound_queue_url=queue_urls[2],
                expected_system_sender_id=required["W1_EXPECTED_SYSTEM_SENDER_ID"],
                region=next(iter(known_regions)),
            )
        except (KeyError, TypeError, ValueError, W1LookupClientError):
            raise SourceRuntimeConfigurationError(
                "source runtime configuration is invalid"
            ) from None


@dataclass(frozen=True)
class SourceRuntimeDelivery:
    """One authenticated SQS delivery, retaining its opaque receipt token."""

    receipt_handle: str
    body: str
    sender_id: str | None


class SqsSourceRuntimeQueue:
    """One-input SQS adapter with a visibility timeout tied to the claim lease."""

    def __init__(
        self,
        client: SqsClient,
        settings: SourceRuntimeSettings,
        *,
        claim_lease_seconds: int,
        input_queue_url: str | None = None,
    ) -> None:
        if (
            not isinstance(claim_lease_seconds, int)
            or isinstance(claim_lease_seconds, bool)
            or not 1 <= claim_lease_seconds <= 43_200
        ):
            raise ValueError("claim lease must be a valid SQS visibility timeout")
        self._client = client
        self._settings = settings
        self._queue_url = input_queue_url or settings.collection_command_queue_url
        if self._queue_url not in {
            settings.collection_command_queue_url,
            settings.commit_gate_command_queue_url,
        }:
            raise ValueError("source runtime input queue is not authorized")
        self._claim_lease_seconds = claim_lease_seconds

    def receive(self) -> tuple[SourceRuntimeDelivery, ...]:
        try:
            response = self._client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=10,
                VisibilityTimeout=self._claim_lease_seconds,
                MessageSystemAttributeNames=["SenderId"],
            )
            messages = response.get("Messages", [])
            if not isinstance(messages, list) or len(messages) > 1:
                raise ValueError
            deliveries: list[SourceRuntimeDelivery] = []
            for message in messages:
                if not isinstance(message, Mapping):
                    raise ValueError
                body = message.get("Body")
                receipt = message.get("ReceiptHandle")
                attributes = message.get("Attributes", {})
                sender = attributes.get("SenderId") if isinstance(attributes, Mapping) else None
                if not isinstance(body, str) or not isinstance(receipt, str) or not receipt:
                    raise ValueError
                deliveries.append(
                    SourceRuntimeDelivery(
                        receipt_handle=receipt,
                        body=body,
                        sender_id=sender if isinstance(sender, str) else None,
                    )
                )
            return tuple(deliveries)
        except Exception:
            raise RuntimeError("source runtime SQS receive unavailable") from None

    def extend_visibility(self, receipt_handle: str) -> None:
        try:
            if not isinstance(receipt_handle, str) or not receipt_handle:
                raise ValueError
            self._client.change_message_visibility(
                QueueUrl=self._queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=self._claim_lease_seconds,
            )
        except Exception:
            raise RuntimeError("source runtime SQS visibility renewal unconfirmed") from None

    def delete(self, receipt_handle: str) -> None:
        try:
            if not isinstance(receipt_handle, str) or not receipt_handle:
                raise ValueError
            self._client.delete_message(QueueUrl=self._queue_url, ReceiptHandle=receipt_handle)
        except Exception:
            raise RuntimeError("source runtime SQS receipt delete unconfirmed") from None

    def send(self, body: str) -> None:
        """Relay only to W1's distinct private inbound queue."""

        try:
            if not isinstance(body, str) or not body:
                raise ValueError
            self._client.send_message(
                QueueUrl=self._settings.private_inbound_queue_url,
                MessageBody=body,
            )
        except Exception:
            raise RuntimeError("source runtime SQS send unavailable") from None


class SqsSourceRuntimeRelayQueue:
    """Send only to W1's physically distinct private inbound queue."""

    def __init__(self, client: SqsClient, settings: SourceRuntimeSettings) -> None:
        self._client = client
        self._queue_url = settings.private_inbound_queue_url

    def send(self, body: str) -> None:
        try:
            if not isinstance(body, str) or not body:
                raise ValueError
            self._client.send_message(QueueUrl=self._queue_url, MessageBody=body)
        except Exception:
            raise RuntimeError("source runtime SQS send unavailable") from None


def _queue_url_region(queue_url: str) -> str | None:
    parsed = urlsplit(queue_url)
    path_parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or len(path_parts) != 2
        or not re.fullmatch(r"[0-9]{12}", path_parts[0])
        or not re.fullmatch(r"[A-Za-z0-9_-]+", path_parts[1])
    ):
        raise ValueError
    matched = _QUEUE_HOST.fullmatch(parsed.hostname.lower())
    return matched.group(1) if matched else None


def _load_runtime_config(settings: SourceRuntimeSettings) -> RuntimeSourceConfigFile:
    try:
        path = Path(settings.runtime_config_file)
        if not path.is_file():
            raise ValueError
        if os.name != "nt" and path.stat().st_mode & 0o022:
            raise ValueError
        raw = path.read_bytes()
        config = parse_runtime_source_config_json(raw)
        if not config.sources:
            raise ValueError
        return config
    except (OSError, SourceRuntimeInputError, ValueError):
        raise SourceRuntimeConfigurationError("source runtime configuration is invalid") from None


def _read_runtime_config(settings: SourceRuntimeSettings) -> int:
    return _load_runtime_config(settings).claim_lease_seconds


def _validate_ca_file(settings: SourceRuntimeSettings) -> None:
    try:
        raw = Path(settings.lookup_ca_file).read_bytes()
        certificates = x509.load_pem_x509_certificates(raw)
        if not certificates:
            raise ValueError
        now = datetime.now(UTC)
        certificates_by_subject: dict[Any, x509.Certificate] = {}
        for certificate in certificates:
            if certificate.subject in certificates_by_subject:
                raise ValueError
            certificates_by_subject[certificate.subject] = certificate
            constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
            if not constraints.ca:
                raise ValueError
            try:
                key_usage = certificate.extensions.get_extension_for_class(x509.KeyUsage).value
            except x509.ExtensionNotFound:
                pass
            else:
                if not key_usage.key_cert_sign:
                    raise ValueError
            if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
                raise ValueError
        for certificate in certificates:
            issuer = certificates_by_subject.get(certificate.issuer)
            if issuer is None:
                raise ValueError
            certificate.verify_directly_issued_by(issuer)
    except Exception:
        raise SourceRuntimeConfigurationError("source runtime configuration is invalid") from None


def _queue_metadata(client: SqsClient, queue_url: str) -> tuple[str, str, str]:
    try:
        attributes = client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn", "RedrivePolicy", "SqsManagedSseEnabled", "KmsMasterKeyId"],
        ).get("Attributes", {})
        if not isinstance(attributes, Mapping):
            raise ValueError
        queue_arn = attributes.get("QueueArn")
        if not isinstance(queue_arn, str):
            raise ValueError
        match = _QUEUE_ARN.fullmatch(queue_arn)
        if match is None:
            raise ValueError
        redrive = json.loads(attributes.get("RedrivePolicy", ""))
        if not isinstance(redrive, Mapping):
            raise ValueError
        dlq_arn = redrive.get("deadLetterTargetArn")
        dlq_match = _QUEUE_ARN.fullmatch(dlq_arn) if isinstance(dlq_arn, str) else None
        max_receive_count = redrive.get("maxReceiveCount")
        if (
            dlq_match is None
            or dlq_arn == queue_arn
            or dlq_match.group("region") != match.group("region")
            or dlq_match.group("account") != match.group("account")
            or isinstance(max_receive_count, bool)
            or not isinstance(max_receive_count, (str, int))
            or not 1 <= int(max_receive_count) <= 1000
            or not (
                attributes.get("SqsManagedSseEnabled") == "true"
                or isinstance(attributes.get("KmsMasterKeyId"), str)
                and bool(attributes["KmsMasterKeyId"].strip())
            )
        ):
            raise ValueError
        return queue_arn, match.group("region"), match.group("account")
    except Exception:
        raise SourceRuntimeConfigurationError("source runtime queue metadata is invalid") from None


def preflight(
    engine: Engine,
    client: SqsClient,
    settings: SourceRuntimeSettings,
) -> dict[str, str]:
    """Validate metadata only; it never receives, sends, deletes, or migrates."""

    _read_runtime_config(settings)
    _validate_ca_file(settings)
    try:
        with engine.connect() as connection:
            database_name = connection.scalar(text("SELECT current_database()"))
            revisions = tuple(
                connection.scalars(text("SELECT version_num FROM alembic_version")).all()
            )
            if (
                not isinstance(database_name, str)
                or not database_name
                or revisions != (_MIGRATION_HEAD,)
            ):
                raise ValueError
    except SourceRuntimeConfigurationError:
        raise
    except Exception:
        raise SourceRuntimeConfigurationError("source runtime database preflight failed") from None

    # URL-only regions are checked before this point by from_environment.  Alias
    # URLs gain their canonical region from their authoritative QueueArn.
    metadata = tuple(
        _queue_metadata(client, queue_url)
        for queue_url in (
            settings.collection_command_queue_url,
            settings.commit_gate_command_queue_url,
            settings.private_inbound_queue_url,
        )
    )
    arns = tuple(item[0] for item in metadata)
    regions = {item[1] for item in metadata}
    if regions != {settings.region}:
        raise SourceRuntimeConfigurationError("source runtime queue regions are inconsistent")
    collection_arn, gate_arn, private_arn = arns
    if private_arn in {collection_arn, gate_arn}:
        raise SourceRuntimeConfigurationError("source runtime private queue topology is invalid")
    return {
        "status": "PREFLIGHT_PASSED",
        "input_mode": "mixed" if collection_arn == gate_arn else "dedicated",
        "scope": "metadata_only_not_permission_or_e2e_proof",
    }


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    """Explicit Task 7 boundaries plus lease values derived from approved config."""

    input_mode: str
    collection_handler: object
    gate_applier: object
    relay_authorizer: object
    private_authority_client: PrivateAuthorityClient
    claim_lease_seconds: int
    visibility_heartbeat_seconds: float


def build_runtime_dependencies(
    settings: SourceRuntimeSettings,
    *,
    client: SqsClient,
    session_factory: object,
    input_mode: str,
    collection_handler: object,
    gate_applier: object,
    relay_lookup_client: object,
    private_authority_client: PrivateAuthorityClient,
    clock: Callable[[], datetime],
    message_id_factory: Callable[[], UUID],
) -> RuntimeDependencies:
    """Bind injected runtime services without hiding them in mutable globals."""

    if input_mode not in {"mixed", "dedicated"}:
        raise SourceRuntimeConfigurationError("source runtime input topology is invalid")
    if not callable(clock) or not callable(message_id_factory):
        raise SourceRuntimeConfigurationError("source runtime dependency is invalid")
    # Keeping these boundaries explicit is intentional even though queue and DB
    # work happens only when run_action invokes the Task 7 runtime.
    if client is None or session_factory is None or private_authority_client is None:
        raise SourceRuntimeConfigurationError("source runtime dependency is invalid")
    runtime_config = _load_runtime_config(settings)
    claim_lease_seconds = runtime_config.claim_lease_seconds
    heartbeat_seconds = runtime_config.heartbeat_interval_seconds
    if not 1 <= claim_lease_seconds <= 43_200 or not 0 < heartbeat_seconds < claim_lease_seconds:
        raise SourceRuntimeConfigurationError("source runtime lease configuration is invalid")
    return RuntimeDependencies(
        input_mode=input_mode,
        collection_handler=collection_handler,
        gate_applier=gate_applier,
        relay_authorizer=build_collection_relay_authorizer(cast(Any, relay_lookup_client)),
        private_authority_client=private_authority_client,
        claim_lease_seconds=claim_lease_seconds,
        visibility_heartbeat_seconds=heartbeat_seconds,
    )


def run_loop(
    consume_once: Callable[[], object],
    relay_once: Callable[[], object],
    stop: Event,
) -> int:
    """Alternate bounded work and stop intake before beginning another action."""

    while not stop.is_set():
        consume_result = consume_once()
        if getattr(consume_result, "status", None) in {"RECEIVE_FAILED", "DELETE_FAILED"}:
            return 1
        if stop.is_set():
            break
        relay_result = relay_once()
        if getattr(relay_result, "status", None) == "SEND_FAILED":
            return 1
    return 0


def run_action(
    action: str,
    *,
    session_factory: object,
    queue: object,
    expected_sender_id: str,
    mode: str,
    collection_handler: object,
    gate_applier: object,
    relay_authorizer: object,
    private_authority_client: PrivateAuthorityClient | None,
    clock: Callable[[], datetime],
    message_id_factory: Callable[[], UUID],
    claim_lease_seconds: int,
    visibility_heartbeat_seconds: float,
    stop: Event | None = None,
) -> object:
    """Execute one bounded Task 7 action or a signal-aware alternating loop."""

    if private_authority_client is None:
        raise SourceRuntimeConfigurationError("source runtime dependency is invalid")

    if action == "consume-once":
        return consume_source_runtime_once(
            cast(Any, session_factory),
            cast(Any, queue),
            expected_sender_id,
            mode=cast(Any, mode),
            collection_handler=cast(Any, collection_handler),
            gate_applier=cast(Any, gate_applier),
            private_authority_client=private_authority_client,
            clock=clock,
            message_id_factory=message_id_factory,
            visibility_heartbeat_seconds=visibility_heartbeat_seconds,
        )
    if action == "relay-once":
        return relay_once(
            cast(Any, session_factory),
            cast(Any, queue),
            clock=clock,
            authority_client=private_authority_client,
            before_send=cast(Any, relay_authorizer),
            claim_lease_seconds=claim_lease_seconds,
        )
    if action != "run":
        raise SourceRuntimeConfigurationError("source runtime action is invalid")
    stop_event = Event() if stop is None else stop
    return run_loop(
        consume_once=lambda: run_action(
            "consume-once",
            session_factory=session_factory,
            queue=queue,
            expected_sender_id=expected_sender_id,
            mode=mode,
            collection_handler=collection_handler,
            gate_applier=gate_applier,
            relay_authorizer=relay_authorizer,
            private_authority_client=private_authority_client,
            clock=clock,
            message_id_factory=message_id_factory,
            claim_lease_seconds=claim_lease_seconds,
            visibility_heartbeat_seconds=visibility_heartbeat_seconds,
        ),
        relay_once=lambda: run_action(
            "relay-once",
            session_factory=session_factory,
            queue=queue,
            expected_sender_id=expected_sender_id,
            mode=mode,
            collection_handler=collection_handler,
            gate_applier=gate_applier,
            relay_authorizer=relay_authorizer,
            private_authority_client=private_authority_client,
            clock=clock,
            message_id_factory=message_id_factory,
            claim_lease_seconds=claim_lease_seconds,
            visibility_heartbeat_seconds=visibility_heartbeat_seconds,
        ),
        stop=stop_event,
    )


def readiness(daemon_active: bool) -> dict[str, str]:
    return {"status": "READY" if daemon_active is True else "NOT_READY"}


def health(daemon_active: bool) -> dict[str, str]:
    return {"status": "HEALTHY" if daemon_active is True else "UNHEALTHY"}


def create_source_runtime_engine(settings: SourceRuntimeSettings) -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c lock_timeout=5000 -c statement_timeout=15000",
        },
    )


def create_sqs_client(settings: SourceRuntimeSettings) -> SqsClient:
    """Use the workload role chain; static AWS credentials are never accepted here."""

    try:
        _require_workload_role_credentials(os.environ)
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
    except Exception:
        raise SourceRuntimeConfigurationError(
            "source runtime AWS credentials are invalid"
        ) from None
    config = importlib.import_module("botocore.config").Config(
        connect_timeout=5,
        read_timeout=15,
        retries={"mode": "standard", "total_max_attempts": 1},
        ignore_configured_endpoint_urls=True,
    )
    return cast(SqsClient, session.client("sqs", region_name=settings.region, config=config))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the preflight gate, then construct every Task 7 boundary explicitly."""

    parser = argparse.ArgumentParser(description="W2 general source-runtime operator")
    parser.add_argument("action", choices=["preflight", "consume-once", "relay-once", "run"])
    args = parser.parse_args(argv)
    engine: Engine | None = None
    try:
        settings = SourceRuntimeSettings.from_environment(os.environ)
        engine = create_source_runtime_engine(settings)
        client = create_sqs_client(settings)
        preflight_result = preflight(engine, client, settings)
        if args.action == "preflight":
            print(
                json.dumps(
                    {"status": "PREFLIGHT_PASSED", "scope": "metadata_only"},
                    sort_keys=True,
                )
            )
            return 0

        runtime_config = _load_runtime_config(settings)
        session_factory = create_session_factory(engine)
        ssl_context = ssl.create_default_context(cafile=settings.lookup_ca_file)
        lookup_client = W1LookupClient(
            endpoint=settings.lookup_endpoint,
            bearer=settings.lookup_bearer,
            ssl_context=ssl_context,
        )
        private_authority_client = W1PrivateAuthorityClient(
            endpoint=settings.lookup_endpoint,
            bearer=settings.lookup_bearer,
            ssl_context=ssl_context,
        )
        input_provider = SqlAlchemyCollectionInputProvider(
            session_factory, runtime_config, onboarding_lookup_client=lookup_client
        )
        collection_handler = partial(
            handle_collection_dispatch,
            session_factory=session_factory,
            lookup_client=lookup_client,
            input_provider=input_provider,
            collector_factory=StaticScrapyCollector,
            parser=extract_static_candidate,
            runtime_config=runtime_config,
            clock=_utc_now,
            uuid_factory=uuid4,
        )
        dependencies = build_runtime_dependencies(
            settings,
            client=client,
            session_factory=session_factory,
            input_mode=preflight_result["input_mode"],
            collection_handler=collection_handler,
            gate_applier=apply_collection_commit_gate,
            relay_lookup_client=lookup_client,
            private_authority_client=private_authority_client,
            clock=_utc_now,
            message_id_factory=uuid4,
        )
        collection_queue = SqsSourceRuntimeQueue(
            client,
            settings,
            claim_lease_seconds=dependencies.claim_lease_seconds,
            input_queue_url=settings.collection_command_queue_url,
        )
        gate_queue = SqsSourceRuntimeQueue(
            client,
            settings,
            claim_lease_seconds=dependencies.claim_lease_seconds,
            input_queue_url=settings.commit_gate_command_queue_url,
        )
        relay_queue = SqsSourceRuntimeRelayQueue(client, settings)

        def consume(queue: object, mode: str) -> object:
            return run_action(
                "consume-once",
                session_factory=session_factory,
                queue=queue,
                expected_sender_id=settings.expected_system_sender_id,
                mode=mode,
                collection_handler=dependencies.collection_handler,
                gate_applier=dependencies.gate_applier,
                relay_authorizer=dependencies.relay_authorizer,
                private_authority_client=dependencies.private_authority_client,
                clock=_utc_now,
                message_id_factory=uuid4,
                claim_lease_seconds=dependencies.claim_lease_seconds,
                visibility_heartbeat_seconds=dependencies.visibility_heartbeat_seconds,
            )

        def relay() -> object:
            return run_action(
                "relay-once",
                session_factory=session_factory,
                queue=relay_queue,
                expected_sender_id=settings.expected_system_sender_id,
                mode="mixed",
                collection_handler=dependencies.collection_handler,
                gate_applier=dependencies.gate_applier,
                relay_authorizer=dependencies.relay_authorizer,
                private_authority_client=dependencies.private_authority_client,
                clock=_utc_now,
                message_id_factory=uuid4,
                claim_lease_seconds=dependencies.claim_lease_seconds,
                visibility_heartbeat_seconds=dependencies.visibility_heartbeat_seconds,
            )

        if args.action == "relay-once":
            outcome = relay()
        elif args.action == "consume-once":
            if dependencies.input_mode == "mixed":
                outcome = consume(collection_queue, "mixed")
            else:
                outcome = consume(collection_queue, "collection")
                if getattr(outcome, "status", None) == "EMPTY":
                    outcome = consume(gate_queue, "gate")
        else:
            stop = Event()
            previous_handlers = {
                sig: signal.signal(sig, lambda _signal, _frame: stop.set())
                for sig in (signal.SIGTERM, signal.SIGINT)
            }
            try:
                if dependencies.input_mode == "mixed":

                    def consume_bounded() -> object:
                        return consume(collection_queue, "mixed")
                else:
                    next_input = 0
                    dedicated = ((collection_queue, "collection"), (gate_queue, "gate"))

                    def consume_bounded() -> object:
                        nonlocal next_input
                        queue, mode = dedicated[next_input]
                        next_input = (next_input + 1) % len(dedicated)
                        return consume(queue, mode)

                return run_loop(consume_bounded, relay, stop)
            finally:
                for sig, previous in previous_handlers.items():
                    signal.signal(sig, previous)

        status = getattr(outcome, "status", "SOURCE_RUNTIME_FAILED")
        print(json.dumps({"status": status}, sort_keys=True))
        return 0 if status in {"APPLIED", "SENT", "EMPTY"} else 1
    except Exception:
        print('{"status":"SOURCE_RUNTIME_FAILED","details":"consult approved operator checks"}')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
