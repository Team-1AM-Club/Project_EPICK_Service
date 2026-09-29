"""Fail-closed, synthetic boundaries for the general source-runtime operator.

These tests intentionally use only fake SQS metadata and SQLAlchemy mocks.  They
must never contact AWS, a W1 endpoint, or a live Source.
"""

from __future__ import annotations

import importlib
import json
import ssl
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest

SOURCE_ID = "11111111-1111-4111-8111-111111111111"
_QUEUE_PREFIX = "arn:aws:sqs:ap-northeast-2:123456789012:"
_CONTAINER_CONFIG_PATH = "/run/epick/source-runtime/config.json"
_CONTAINER_CA_PATH = "/run/epick/source-runtime/w1-ca.pem"
_PARSEABLE_CA_CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIICiTCCAg+gAwIBAgIQH0evqmIAcFBUTAGem2OZKjAKBggqhkjOPQQDAzCBhTEL
MAkGA1UEBhMCR0IxGzAZBgNVBAgTEkdyZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UE
BxMHU2FsZm9yZDEaMBgGA1UEChMRQ09NT0RPIENBIExpbWl0ZWQxKzApBgNVBAMT
IkNPTU9ETyBFQ0MgQ2VydGlmaWNhdGlvbiBBdXRob3JpdHkwHhcNMDgwMzA2MDAw
MDAwWhcNMzgwMTE4MjM1OTU5WjCBhTELMAkGA1UEBhMCR0IxGzAZBgNVBAgTEkdy
ZWF0ZXIgTWFuY2hlc3RlcjEQMA4GA1UEBxMHU2FsZm9yZDEaMBgGA1UEChMRQ09N
T0RPIENBIExpbWl0ZWQxKzApBgNVBAMTIkNPTU9ETyBFQ0MgQ2VydGlmaWNhdGlv
biBBdXRob3JpdHkwdjAQBgcqhkjOPQIBBgUrgQQAIgNiAAQDR3svdcmCFYX7deSR
FtSrYpn1PlILBs5BAH+X4QokPB0BBO490o0JlwzgdeT6+3eKKvUDYEs2ixYjFq0J
cfRK9ChQtP6IHG4/bC8vCVlbpVsLM5niwz2J+Wos77LTBumjQjBAMB0GA1UdDgQW
BBR1cacZSBm8nZ3qQUfflMRId5nTeTAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/
BAUwAwEB/zAKBggqhkjOPQQDAwNoADBlAjEA7wNbeqy3eApyt4jf/7VGFAkK+qDm
fQjGGoe9GKhzvSbKYAydzpmfz1wPMOG+FDHqAjAU9JM8SaczepBGR7NjfRObTrdv
GDeAU/7dIOA1mjbRxwG55tzd8/8dLDoWV9mSOdY=
-----END CERTIFICATE-----
"""


def _operator() -> Any:
    """Load the future operator lazily so the initial RED state is an assertion."""
    try:
        return importlib.import_module("epick_engine.source_collection.source_runtime_operator")
    except ModuleNotFoundError as exc:
        pytest.fail(f"source runtime operator is not implemented: {exc.name}")


def _symbol(name: str) -> Any:
    operator = _operator()
    try:
        return getattr(operator, name)
    except AttributeError:
        pytest.fail(f"source runtime operator must export {name}")


def _config_payload() -> dict[str, object]:
    return {
        "schema_version": "w2.source-runtime-config.v1",
        "claim_lease_seconds": 120,
        "sources": {
            SOURCE_ID: {
                "policy_revision": 3,
                "robots_permission": "allowed",
                "result_version": 1,
                "language": "ko",
                "redirect_robots_permissions": [],
                "limits": {
                    "site_concurrency": 1,
                    "global_concurrency": 2,
                    "source_ttl_seconds": 300,
                    "max_response_bytes": 1_048_576,
                    "max_decompressed_bytes": 2_097_152,
                    "connect_timeout_seconds": 3.0,
                    "read_timeout_seconds": 5.0,
                    "max_redirects": 2,
                    "general_retry_limit": 0,
                    "retention_days": 7,
                },
            }
        },
    }


def _write_runtime_files(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "source-runtime-config.json"
    config_path.write_text(json.dumps(_config_payload()), encoding="utf-8")
    ca_path = tmp_path / "w1-ca.pem"
    ca_path.write_text(_PARSEABLE_CA_CERTIFICATE, encoding="ascii")
    ssl.create_default_context(cafile=str(ca_path))
    return config_path, ca_path


def _map_fixed_container_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path]:
    """Expose temporary test files only behind the fixed in-container paths."""
    config_path, ca_path = _write_runtime_files(tmp_path)
    path_constructor = Path

    def mapped_path(value: str) -> Path:
        if value == _CONTAINER_CONFIG_PATH:
            return config_path
        if value == _CONTAINER_CA_PATH:
            return ca_path
        return path_constructor(value)

    monkeypatch.setattr(_operator(), "Path", mapped_path)
    return config_path, ca_path


def environment(
    *,
    config_file: str = _CONTAINER_CONFIG_PATH,
    ca_file: str = _CONTAINER_CA_PATH,
    **overrides: str,
) -> dict[str, str]:
    values = {
        "EPICK_DATABASE_URL": "postgresql+psycopg://operator:DSN-CANARY@localhost/epick",
        "W2_SOURCE_RUNTIME_CONFIG_FILE": config_file,
        "W1_LOOKUP_ENDPOINT": ("https://lookup.example.test/internal/v1/job-commands/lookup"),
        "W1_LOOKUP_BEARER": "BEARER-CANARY",
        "W1_LOOKUP_CA_FILE": ca_file,
        "W1_COLLECTION_COMMAND_QUEUE_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/collection"
        ),
        "W1_COMMIT_GATE_COMMAND_QUEUE_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/commit-gate"
        ),
        "W1_PRIVATE_INBOUND_QUEUE_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/private-inbound"
        ),
        "W1_EXPECTED_SYSTEM_SENDER_ID": "AROASYNTHETICW1ROLE12",
    }
    values.update(overrides)
    return values


def _settings(values: dict[str, str]) -> Any:
    return _symbol("SourceRuntimeSettings").from_environment(values)


def _configuration_error() -> type[Exception]:
    return _symbol("SourceRuntimeConfigurationError")


class FakeSqs:
    """Records only SDK calls made through the source-runtime SQS adapter."""

    def __init__(self, attributes: dict[str, dict[str, str]]) -> None:
        self.attributes = attributes
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.receive_response: dict[str, Any] = {"Messages": []}

    def get_queue_attributes(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("attributes", kwargs))
        return {"Attributes": self.attributes[kwargs["QueueUrl"]]}

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("receive", kwargs))
        return self.receive_response

    def change_message_visibility(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("visibility", kwargs))
        return {}

    def delete_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete", kwargs))
        return {}

    def send_message(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("send", kwargs))
        return {"MessageId": "synthetic"}


def _queue_attributes(*, arn_name: str, encrypted: bool = True, dlq: bool = True) -> dict[str, str]:
    attributes = {
        "QueueArn": _QUEUE_PREFIX + arn_name,
        "SqsManagedSseEnabled": "true" if encrypted else "false",
    }
    if dlq:
        attributes["RedrivePolicy"] = json.dumps(
            {"deadLetterTargetArn": _QUEUE_PREFIX + arn_name + "-dlq", "maxReceiveCount": "5"}
        )
    return attributes


def _engine(*, revisions: tuple[str, ...] = ("0013_deletion_ack_confirmed",)) -> MagicMock:
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.scalar.return_value = "epick"
    connection.scalars.return_value.all.return_value = list(revisions)
    return engine


def _fake_sqs(
    values: dict[str, str], *, gate_arn: str = "commit-gate", private_arn: str = "private"
) -> FakeSqs:
    return FakeSqs(
        {
            values["W1_COLLECTION_COMMAND_QUEUE_URL"]: _queue_attributes(arn_name="collection"),
            values["W1_COMMIT_GATE_COMMAND_QUEUE_URL"]: _queue_attributes(arn_name=gate_arn),
            values["W1_PRIVATE_INBOUND_QUEUE_URL"]: _queue_attributes(arn_name=private_arn),
        }
    )


@pytest.mark.parametrize("key", list(environment()))
def test_all_live_settings_require_explicit_nonempty_values(key: str) -> None:
    values = environment()
    values.pop(key)

    with pytest.raises(_configuration_error()) as caught:
        _settings(values)

    diagnostic = str(caught.value)
    assert "DSN-CANARY" not in diagnostic
    assert "BEARER-CANARY" not in diagnostic
    assert "QUERY-CANARY" not in diagnostic


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("EPICK_DATABASE_URL", "sqlite:///DSN-CANARY.db"),
        ("W1_LOOKUP_ENDPOINT", "http://LOOKUP-CANARY.invalid/private"),
        ("W1_LOOKUP_BEARER", ""),
        ("W1_EXPECTED_SYSTEM_SENDER_ID", " "),
        ("W1_COLLECTION_COMMAND_QUEUE_URL", "http://queue.example.test/QUEUE-CANARY"),
    ],
)
def test_unsafe_live_settings_fail_closed_without_echoing_values(key: str, value: str) -> None:
    values = environment(**{key: value})

    with pytest.raises(_configuration_error()) as caught:
        _settings(values)

    diagnostic = str(caught.value)
    assert "DSN-CANARY" not in diagnostic
    assert "BEARER-CANARY" not in diagnostic
    assert "QUERY-CANARY" not in diagnostic
    assert "LOOKUP-CANARY" not in diagnostic
    assert "QUEUE-CANARY" not in diagnostic


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://lookup.example.test/v1/lookup",
        "https://lookup.example.test/internal/v1/job-commands/lookup?query=1",
        "https://lookup.example.test/internal/v1/job-commands/lookup#fragment",
        "https://user@lookup.example.test/internal/v1/job-commands/lookup",
    ),
)
def test_settings_reject_noncanonical_lookup_endpoint(endpoint: str) -> None:
    with pytest.raises(_configuration_error()):
        _settings(environment(W1_LOOKUP_ENDPOINT=endpoint))


def test_settings_repr_redacts_dsn_bearer_and_endpoint_query() -> None:
    settings = _settings(environment())

    rendered = repr(settings)
    assert "DSN-CANARY" not in rendered
    assert "BEARER-CANARY" not in rendered
    assert "QUERY-CANARY" not in rendered


@pytest.mark.parametrize("key", ("W2_SOURCE_RUNTIME_CONFIG_FILE", "W1_LOOKUP_CA_FILE"))
def test_settings_reject_host_path_overrides_for_container_mounts(tmp_path: Path, key: str) -> None:
    config_path, ca_path = _write_runtime_files(tmp_path)
    values = environment(**{key: str(config_path if key.endswith("CONFIG_FILE") else ca_path)})

    with pytest.raises(_configuration_error()) as caught:
        _settings(values)

    assert str(config_path) not in str(caught.value)
    assert str(ca_path) not in str(caught.value)


@pytest.mark.parametrize(
    "key",
    (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
    ),
)
def test_settings_reject_static_aws_credentials_and_profiles(key: str) -> None:
    values = environment(**{key: "AWS-CREDENTIAL-CANARY"})

    with pytest.raises(_configuration_error()) as caught:
        _settings(values)

    assert "AWS-CREDENTIAL-CANARY" not in str(caught.value)


def test_preflight_uses_queue_arn_not_url_spelling_for_shared_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _map_fixed_container_paths(monkeypatch, tmp_path)
    values = environment(
        W1_COLLECTION_COMMAND_QUEUE_URL=(
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/shared?endpoint=one"
        ),
        W1_COMMIT_GATE_COMMAND_QUEUE_URL=(
            "https://queue.example.test/123456789012/shared?endpoint=two"
        ),
    )
    sdk = FakeSqs(
        {
            values["W1_COLLECTION_COMMAND_QUEUE_URL"]: _queue_attributes(arn_name="shared"),
            values["W1_COMMIT_GATE_COMMAND_QUEUE_URL"]: _queue_attributes(arn_name="shared"),
            values["W1_PRIVATE_INBOUND_QUEUE_URL"]: _queue_attributes(arn_name="private"),
        }
    )

    result = _symbol("preflight")(_engine(), sdk, _settings(values))

    assert result["status"] == "PREFLIGHT_PASSED"
    assert result["input_mode"] == "mixed"
    assert {name for name, _ in sdk.calls} == {"attributes"}


def test_preflight_uses_dedicated_consumers_for_distinct_input_arns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _map_fixed_container_paths(monkeypatch, tmp_path)
    values = environment()
    sdk = _fake_sqs(values)

    result = _symbol("preflight")(_engine(), sdk, _settings(values))

    assert result["status"] == "PREFLIGHT_PASSED"
    assert result["input_mode"] == "dedicated"
    assert {name for name, _ in sdk.calls} == {"attributes"}


@pytest.mark.parametrize("max_receive_count", (5, "5"))
def test_preflight_accepts_numeric_or_string_redrive_max_receive_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, max_receive_count: int | str
) -> None:
    _map_fixed_container_paths(monkeypatch, tmp_path)
    values = environment()
    sdk = _fake_sqs(values)
    sdk.attributes[values["W1_COLLECTION_COMMAND_QUEUE_URL"]]["RedrivePolicy"] = json.dumps(
        {
            "deadLetterTargetArn": _QUEUE_PREFIX + "collection-dlq",
            "maxReceiveCount": max_receive_count,
        }
    )

    result = _symbol("preflight")(_engine(), sdk, _settings(values))

    assert result["status"] == "PREFLIGHT_PASSED"


@pytest.mark.parametrize(
    "migration_revisions",
    [
        pytest.param(("0009_private_deletion_receipt",), id="previous-head"),
        pytest.param(("0010_private_deletion_scope_v2",), id="pre-ack-retention-head"),
        pytest.param(("0011_private_ack_control_retention",), id="pre-ack-digest-head"),
        pytest.param(("0012_private_ack_wire_digest",), id="pre-deletion-ack-head"),
        pytest.param(("9999_unknown",), id="unknown-head"),
        pytest.param(
            ("0013_deletion_ack_confirmed", "9999_unknown"),
            id="multiple-heads",
        ),
        pytest.param(
            ("0013_deletion_ack_confirmed", "0013_deletion_ack_confirmed"),
            id="duplicate-head",
        ),
    ],
)
def test_preflight_rejects_incompatible_migration_heads_before_sqs_metadata(
    migration_revisions: tuple[str, ...], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _map_fixed_container_paths(monkeypatch, tmp_path)
    values = environment()
    sdk = _fake_sqs(values)

    with pytest.raises(_configuration_error()):
        _symbol("preflight")(
            _engine(revisions=migration_revisions),
            sdk,
            _settings(values),
        )

    assert sdk.calls == []


def test_preflight_rejects_pre_scope_migration_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _map_fixed_container_paths(monkeypatch, tmp_path)
    values = environment()
    sdk = _fake_sqs(values)

    with pytest.raises(_configuration_error()):
        _symbol("preflight")(
            _engine(revisions=("0008_collection_runtime",)),
            sdk,
            _settings(values),
        )

    assert sdk.calls == []


def test_preflight_rejects_cross_region_before_any_sqs_operation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _map_fixed_container_paths(monkeypatch, tmp_path)
    values = environment(
        W1_COMMIT_GATE_COMMAND_QUEUE_URL=(
            "https://sqs.us-east-1.amazonaws.com/123456789012/commit-gate"
        ),
    )
    sdk = _fake_sqs(values)

    with pytest.raises(_configuration_error()):
        _symbol("preflight")(_engine(), sdk, _settings(values))

    assert sdk.calls == []


def test_preflight_rejects_private_input_collision_by_physical_queue_arn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _map_fixed_container_paths(monkeypatch, tmp_path)
    values = environment()
    sdk = _fake_sqs(values, private_arn="collection")

    with pytest.raises(_configuration_error()):
        _symbol("preflight")(_engine(), sdk, _settings(values))

    assert {name for name, _ in sdk.calls} == {"attributes"}


@pytest.mark.parametrize(
    ("encrypted", "dlq"),
    [(False, True), (True, False)],
)
def test_preflight_rejects_unencrypted_or_undeliverable_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, encrypted: bool, dlq: bool
) -> None:
    _map_fixed_container_paths(monkeypatch, tmp_path)
    values = environment()
    sdk = _fake_sqs(values)
    sdk.attributes[values["W1_COLLECTION_COMMAND_QUEUE_URL"]] = _queue_attributes(
        arn_name="collection", encrypted=encrypted, dlq=dlq
    )

    with pytest.raises(_configuration_error()):
        _symbol("preflight")(_engine(), sdk, _settings(values))

    assert {name for name, _ in sdk.calls} == {"attributes"}


@pytest.mark.parametrize(
    "contents",
    (
        "-----BEGIN CERTIFICATE-----\nsynthetic\n-----END CERTIFICATE-----\n",
        "-----BEGIN CERTIFICATE-----\ninvalid-base64\n-----END CERTIFICATE-----\n",
    ),
)
def test_preflight_rejects_marker_only_or_unparseable_ca_before_db_or_sqs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, contents: str
) -> None:
    _, ca_path = _map_fixed_container_paths(monkeypatch, tmp_path)
    ca_path.write_text(contents, encoding="ascii")
    values = environment()
    sdk = _fake_sqs(values)
    engine = _engine()

    with pytest.raises(_configuration_error()):
        _symbol("preflight")(engine, sdk, _settings(values))

    assert sdk.calls == []
    assert engine.connect.call_count == 0


def test_sqs_adapter_preserves_sender_receipt_and_uses_claim_lease_visibility() -> None:
    values = environment()
    sdk = _fake_sqs(values)
    sdk.receive_response = {
        "Messages": [
            {
                "Body": '{"message_type":"collection.dispatch"}',
                "ReceiptHandle": "RECEIPT-CANARY",
                "Attributes": {"SenderId": "AROASYNTHETICW1ROLE12:worker"},
            }
        ]
    }
    queue = _symbol("SqsSourceRuntimeQueue")(sdk, _settings(values), claim_lease_seconds=120)

    messages = queue.receive()
    queue.extend_visibility(messages[0].receipt_handle)

    assert messages[0].sender_id == "AROASYNTHETICW1ROLE12:worker"
    assert messages[0].receipt_handle == "RECEIPT-CANARY"
    assert sdk.calls[0][0] == "receive"
    assert sdk.calls[0][1]["MessageSystemAttributeNames"] == ["SenderId"]
    assert sdk.calls[1] == (
        "visibility",
        {
            "QueueUrl": values["W1_COLLECTION_COMMAND_QUEUE_URL"],
            "ReceiptHandle": "RECEIPT-CANARY",
            "VisibilityTimeout": 120,
        },
    )
    assert all(name not in {"delete", "send"} for name, _ in sdk.calls)


class _RecordingQueue:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)


def _fixed_clock() -> datetime:
    return datetime(2026, 9, 21, tzinfo=UTC)


@pytest.mark.parametrize("input_mode", ("mixed", "dedicated"))
def test_build_runtime_dependencies_keeps_all_runtime_boundaries_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, input_mode: str
) -> None:
    _map_fixed_container_paths(monkeypatch, tmp_path)
    operator = _operator()
    values = environment()

    def collection_handler(_: object) -> None:
        return None

    def gate_applier(*_: object, **__: object) -> object:
        return object()

    relay_lookup_client = object()
    private_authority_client = object()

    def message_id_factory() -> UUID:
        return UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    dependencies = _symbol("build_runtime_dependencies")(
        _settings(values),
        client=_fake_sqs(values),
        session_factory=object(),
        input_mode=input_mode,
        collection_handler=collection_handler,
        gate_applier=gate_applier,
        relay_lookup_client=relay_lookup_client,
        private_authority_client=private_authority_client,
        clock=_fixed_clock,
        message_id_factory=message_id_factory,
    )

    assert dependencies.input_mode == input_mode
    assert dependencies.collection_handler is collection_handler
    assert dependencies.gate_applier is gate_applier
    assert dependencies.relay_authorizer is not None
    assert dependencies.private_authority_client is private_authority_client
    assert dependencies.claim_lease_seconds == 120
    assert 0 < dependencies.visibility_heartbeat_seconds <= 40
    assert operator is _operator()


@pytest.mark.parametrize("mode", ("mixed", "collection", "gate"))
def test_production_actions_inject_private_authority_client(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    operator = _operator()
    queue = _RecordingQueue()
    session_factory = object()
    collection_calls: list[object] = []
    gate_calls: list[tuple[object, object]] = []
    relay_calls: list[tuple[str, dict[str, object]]] = []
    authority_clients: list[object] = []
    captured: dict[str, object] = {}
    private_authority_client = object()

    def collection_handler(dispatch: object) -> None:
        collection_calls.append(dispatch)

    def gate_applier(session: object, gate: object, **_: object) -> object:
        gate_calls.append((session, gate))
        return object()

    def relay_authorizer(kind: str, payload: dict[str, object]) -> None:
        relay_calls.append((kind, payload))

    def fake_consume(*args: object, **kwargs: object) -> object:
        captured["consume_args"] = args
        captured["consume_kwargs"] = kwargs
        assert kwargs["collection_handler"] is collection_handler
        assert kwargs["gate_applier"] is gate_applier
        authority_clients.append(kwargs["private_authority_client"])
        return SimpleNamespace(status="REJECTED")

    def fake_relay(*args: object, **kwargs: object) -> object:
        captured["relay_args"] = args
        captured["relay_kwargs"] = kwargs
        authorizer = kwargs["before_send"]
        assert authorizer is relay_authorizer
        authorizer("ACK", {})
        authority_clients.append(kwargs["authority_client"])
        return SimpleNamespace(status="EMPTY")

    def fake_run_loop(*, consume_once: Any, relay_once: Any, **_: object) -> int:
        consume_once()
        relay_once()
        return 0

    monkeypatch.setattr(operator, "consume_source_runtime_once", fake_consume, raising=False)
    monkeypatch.setattr(operator, "relay_once", fake_relay, raising=False)
    monkeypatch.setattr(operator, "run_loop", fake_run_loop, raising=False)

    common = {
        "session_factory": session_factory,
        "queue": queue,
        "expected_sender_id": "AROASYNTHETICW1ROLE12",
        "mode": mode,
        "collection_handler": collection_handler,
        "gate_applier": gate_applier,
        "relay_authorizer": relay_authorizer,
        "private_authority_client": private_authority_client,
        "clock": _fixed_clock,
        "message_id_factory": lambda: UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        "claim_lease_seconds": 120,
        "visibility_heartbeat_seconds": 40,
    }

    consume_result = _symbol("run_action")("consume-once", **common)
    relay_result = _symbol("run_action")("relay-once", **common)
    run_result = _symbol("run_action")("run", stop=Event(), **common)

    assert consume_result.status == "REJECTED"
    assert relay_result.status == "EMPTY"
    assert run_result == 0
    assert captured["consume_args"] == (session_factory, queue, "AROASYNTHETICW1ROLE12")
    assert captured["consume_kwargs"] == {
        "mode": mode,
        "collection_handler": collection_handler,
        "gate_applier": gate_applier,
        "private_authority_client": private_authority_client,
        "clock": _fixed_clock,
        "message_id_factory": common["message_id_factory"],
        "visibility_heartbeat_seconds": 40,
    }
    assert captured["relay_args"] == (session_factory, queue)
    assert captured["relay_kwargs"] == {
        "clock": _fixed_clock,
        "authority_client": private_authority_client,
        "before_send": relay_authorizer,
        "claim_lease_seconds": 120,
    }
    assert relay_calls == [("ACK", {}), ("ACK", {})]
    assert collection_calls == []
    assert gate_calls == []
    assert queue.deleted == []
    assert authority_clients == [private_authority_client] * 4


def test_run_action_run_delegates_only_explicit_bounded_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = _operator()
    captured: dict[str, object] = {}
    stop = Event()

    def fake_run_loop(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    def collection_handler(_: object) -> None:
        return None

    def gate_applier(*_: object, **__: object) -> object:
        return object()

    def relay_authorizer(_: str, __: dict[str, object]) -> None:
        return None

    private_authority_client = object()

    monkeypatch.setattr(operator, "run_loop", fake_run_loop, raising=False)
    result = _symbol("run_action")(
        "run",
        session_factory=object(),
        queue=_RecordingQueue(),
        expected_sender_id="AROASYNTHETICW1ROLE12",
        mode="mixed",
        collection_handler=collection_handler,
        gate_applier=gate_applier,
        relay_authorizer=relay_authorizer,
        private_authority_client=private_authority_client,
        clock=_fixed_clock,
        message_id_factory=lambda: UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        claim_lease_seconds=120,
        visibility_heartbeat_seconds=40,
        stop=stop,
    )

    assert result == 0
    assert captured["stop"] is stop
    assert callable(captured["consume_once"])
    assert callable(captured["relay_once"])


@pytest.mark.parametrize("action", ("consume-once", "relay-once", "run"))
def test_missing_authority_dependency_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    operator = _operator()
    runtime_calls: list[str] = []

    monkeypatch.setattr(
        operator,
        "consume_source_runtime_once",
        lambda *_args, **_kwargs: runtime_calls.append("consume"),
    )
    monkeypatch.setattr(
        operator,
        "relay_once",
        lambda *_args, **_kwargs: runtime_calls.append("relay"),
    )
    monkeypatch.setattr(
        operator,
        "run_loop",
        lambda **_kwargs: runtime_calls.append("run"),
    )

    with pytest.raises(_configuration_error(), match="dependency is invalid"):
        _symbol("run_action")(
            action,
            session_factory=object(),
            queue=_RecordingQueue(),
            expected_sender_id="AROASYNTHETICW1ROLE12",
            mode="mixed",
            collection_handler=lambda _dispatch: None,
            gate_applier=lambda *_args, **_kwargs: object(),
            relay_authorizer=lambda _kind, _payload: None,
            private_authority_client=None,
            clock=_fixed_clock,
            message_id_factory=lambda: UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            claim_lease_seconds=120,
            visibility_heartbeat_seconds=40,
        )

    assert runtime_calls == []


def test_run_loop_honors_graceful_signal_stop_after_current_bounded_action() -> None:
    stop = Event()
    calls: list[str] = []

    def consume_once() -> None:
        calls.append("consume")
        stop.set()

    def relay_once() -> None:
        calls.append("relay")

    result = _symbol("run_loop")(
        consume_once=consume_once,
        relay_once=relay_once,
        stop=stop,
    )

    assert result == 0
    assert calls == ["consume"]


def test_readiness_and_health_require_an_explicit_active_daemon() -> None:
    readiness = _symbol("readiness")
    health = _symbol("health")

    assert readiness(daemon_active=False)["status"] != "READY"
    assert health(daemon_active=False)["status"] != "HEALTHY"
    assert readiness(daemon_active=True)["status"] == "READY"
    assert health(daemon_active=True)["status"] == "HEALTHY"


def test_create_sqs_client_sets_finite_connect_and_read_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = _operator()
    create_sqs_client = _symbol("create_sqs_client")
    settings = _settings(environment())
    captured: dict[str, object] = {}
    client = object()
    provider = SimpleNamespace(METHOD=next(iter(operator._WORKLOAD_ROLE_PROVIDER_METHODS)))
    botocore_session = SimpleNamespace(
        get_component=lambda _: SimpleNamespace(providers=[provider]),
    )

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            captured["config"] = kwargs

    class FakeBotoSession:
        def __init__(self, *, botocore_session: object) -> None:
            assert botocore_session is not None

        def client(self, service_name: str, **kwargs: object) -> object:
            captured["client"] = (service_name, kwargs)
            return client

    def fake_import_module(name: str) -> object:
        if name == "boto3":
            return SimpleNamespace(Session=FakeBotoSession)
        if name == "botocore.session":
            return SimpleNamespace(get_session=lambda: botocore_session)
        if name == "botocore.config":
            return SimpleNamespace(Config=FakeConfig)
        raise AssertionError(name)

    monkeypatch.setattr(operator, "_require_workload_role_credentials", lambda _: None)
    monkeypatch.setattr(operator.importlib, "import_module", fake_import_module)

    assert create_sqs_client(settings) is client
    config = captured["config"]
    assert isinstance(config, dict)
    for key in ("connect_timeout", "read_timeout"):
        assert isinstance(config[key], int | float)
        assert 0 < config[key] < 60
