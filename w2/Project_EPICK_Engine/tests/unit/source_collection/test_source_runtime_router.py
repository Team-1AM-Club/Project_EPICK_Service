"""TDD contract for the single-consumer source-runtime router."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import pytest

from epick_engine.source_collection.commit_gate_contracts import build_staged_result
from epick_engine.source_collection.commit_gate_runtime import QueueDelivery
from epick_engine.source_collection.commit_gate_store import (
    PrivateCommitGateAck,
    PrivateCommitStage,
)
from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import PrivateGateAuthority
from epick_engine.source_collection.source_runtime import (
    RuntimeAuthorizationError,
    build_collection_relay_authorizer,
    consume_source_runtime_once,
)
from epick_engine.source_collection.w1_private_authority_contracts import (
    GateAuthorityResponse,
    GateScopeLookupResponse,
    W1GateBinding,
)
from epick_engine.source_collection.w1_transport import LookupRequest, LookupResponse

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "w1_private_contract"
EXPECTED_SENDER_ID = "w1-source-runtime"
NOW = datetime(2026, 9, 20, tzinfo=UTC)


@dataclass
class FakeQueue:
    deliveries: list[QueueDelivery] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    visibility_receipts: list[str] = field(default_factory=list)

    def receive(self) -> list[QueueDelivery]:
        return self.deliveries[:1]

    def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    def extend_visibility(self, receipt_handle: str) -> None:
        self.visibility_receipts.append(receipt_handle)


class NeverSessionFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        raise AssertionError("collection routing must not open a gate DB session")

    def begin(self) -> AbstractContextManager[object]:
        self.calls += 1
        raise AssertionError("collection routing must not open a gate DB transaction")


class RecordingCollectionHandler:
    def __init__(self) -> None:
        self.dispatches: list[object] = []

    def __call__(self, dispatch: object) -> None:
        self.dispatches.append(dispatch)


class UnexpectedCallable:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("this dependency must not be reached")


@dataclass
class PersistedAck:
    delivered_at: datetime | None = NOW


@dataclass(frozen=True)
class AppliedAck:
    message_id: UUID


class GateSession:
    def __init__(self) -> None:
        self.persisted_ack: PersistedAck | None = None

    def __enter__(self) -> GateSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, model: object, _message_id: object, **_kwargs: object) -> object | None:
        if model is PrivateCommitStage:
            return None
        if model is PrivateCommitGateAck:
            return self.persisted_ack
        raise AssertionError("unexpected gate session model")


class GateSessionFactory:
    def __init__(self) -> None:
        self.session = GateSession()
        self.begin_calls = 0

    def begin(self) -> GateSession:
        self.begin_calls += 1
        return self.session

    def __call__(self) -> GateSession:
        return self.session


class RecordingGateApplier:
    def __init__(self) -> None:
        self.gates: list[object] = []

    def __call__(
        self,
        session: GateSession,
        gate: object,
        *,
        ack_message_id: UUID,
        occurred_at: datetime,
        private_gate_authority: PrivateGateAuthority,
    ) -> object:
        assert isinstance(ack_message_id, UUID)
        assert occurred_at == NOW
        assert private_gate_authority.authority_ref == "test:w1-gate-authority"
        self.gates.append(gate)
        session.persisted_ack = PersistedAck()
        return AppliedAck(message_id=ack_message_id)


class RecordingRelayLookup:
    def __init__(self, response: LookupResponse) -> None:
        self._response = response
        self.requests: list[LookupRequest] = []

    def lookup(self, request: LookupRequest) -> LookupResponse:
        self.requests.append(request)
        return self._response


class NeverRelayLookup:
    def __init__(self) -> None:
        self.calls = 0

    def lookup(self, _request: LookupRequest) -> LookupResponse:
        self.calls += 1
        raise AssertionError("this relay path must not perform a W1 lookup")


def _gate_payload(binding: W1GateBinding, phase: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "owner_user_id": str(binding.private.owner_user_id),
        "owner_deletion_epoch": binding.private.owner_deletion_epoch,
        "command_id": str(binding.private.command_id),
        "job_id": str(binding.private.job_id),
        "execution_fence": binding.private.execution_fence,
        "operation_id": str(binding.operation_id),
        "operation_revision": binding.operation_revision,
        "action": binding.action,
        "phase": phase,
        "result_digest": binding.result_digest,
    }
    if binding.purge_owner_deletion_epoch is not None:
        payload["purge_owner_deletion_epoch"] = binding.purge_owner_deletion_epoch
    return payload


class FakePrivateAuthorityClient:
    def lookup_gate_scope(
        self,
        gate: W1GateBinding,
        phase: str,
    ) -> GateScopeLookupResponse:
        return GateScopeLookupResponse.model_validate(
            {
                "schema_version": "w1.private.w2-gate-scope-lookup.v1",
                **_gate_payload(gate, phase),
                "scope": {"type": "ACCOUNT"},
            }
        )

    def authorize_gate(
        self,
        gate: W1GateBinding,
        phase: str,
        scope: PrivateDeletionScope,
    ) -> GateAuthorityResponse:
        return GateAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-gate-authority.v1",
                **_gate_payload(gate, phase),
                "scope": scope.to_mapping(),
                "authority_ref": "test:w1-gate-authority",
            }
        )


class HeartbeatQueue(FakeQueue):
    def __init__(
        self, deliveries: list[QueueDelivery], *, fail_on_extension: int | None = None
    ) -> None:
        super().__init__(deliveries)
        self.fail_on_extension = fail_on_extension
        self.visibility_receipts: list[str] = []
        self.second_extension = Event()
        self.extension_failed = Event()
        self.handler_finished = Event()
        self.delete_after_handler = False
        self.extension_after_delete = Event()
        self._delete_seen = Event()

    def extend_visibility(self, receipt_handle: str) -> None:
        if self._delete_seen.is_set():
            self.extension_after_delete.set()
        self.visibility_receipts.append(receipt_handle)
        if len(self.visibility_receipts) >= 2:
            self.second_extension.set()
        if (
            self.fail_on_extension is not None
            and len(self.visibility_receipts) >= self.fail_on_extension
        ):
            self.extension_failed.set()
            raise RuntimeError("synthetic receipt visibility loss")

    def delete(self, receipt_handle: str) -> None:
        self.delete_after_handler = self.handler_finished.is_set()
        self._delete_seen.set()
        super().delete(receipt_handle)


def _delivery(fixture_name: str, *, sender_id: str | None = None) -> QueueDelivery:
    body = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    return QueueDelivery(
        receipt_handle=f"receipt-{fixture_name}",
        body=body,
        sender_id=sender_id or f"{EXPECTED_SENDER_ID}:synthetic-session",
    )


def _staged_payload() -> tuple[dict[str, object], CollectionCommand]:
    raw = json.loads(
        (FIXTURES.parent / "w2_commit_gate_proposal/digest-vector.json").read_text(encoding="utf-8")
    )
    command_id, job_id, owner_ref = uuid4(), uuid4(), uuid4()
    raw["command"].update(
        command_id=str(command_id),
        job_id=str(job_id),
        authenticated_owner_ref=str(owner_ref),
    )
    raw["result"].update(command_id=str(command_id), job_id=str(job_id))
    command = CollectionCommand.model_validate(raw["command"])
    result = CollectionResult.model_validate(raw["result"])
    proposal = build_staged_result(
        command,
        result,
        message_id=uuid4(),
        occurred_at=NOW,
    )
    return proposal.model_dump(mode="json"), command


def _available_lookup(command: CollectionCommand) -> LookupResponse:
    return LookupResponse(
        schema_version="w1.private.command-lookup.v1",
        command_id=command.command_id,
        status="AVAILABLE",
        reason_code=None,
        command=command,
    )


def _consume(
    queue: FakeQueue,
    session_factory: object,
    collection_handler: object,
    *,
    mode: str,
    gate_applier: object = UnexpectedCallable(),
    visibility_heartbeat_seconds: float | None = 0.01,
) -> object:
    def bound_collection_handler(
        dispatch: object,
        *,
        private_authority_client: object,
    ) -> object:
        assert private_authority_client is collection_authority_client
        return collection_handler(dispatch)

    collection_authority_client = FakePrivateAuthorityClient()

    return consume_source_runtime_once(
        session_factory,
        queue,
        EXPECTED_SENDER_ID,
        mode=mode,
        collection_handler=bound_collection_handler,
        gate_applier=gate_applier,
        private_authority_client=collection_authority_client,
        clock=lambda: NOW,
        visibility_heartbeat_seconds=visibility_heartbeat_seconds,
    )


def test_mixed_router_routes_each_collection_dispatch_once_without_gate_db_work() -> None:
    """Removing exact collection message-type routing must fail this test."""

    handler = RecordingCollectionHandler()
    session_factory = NeverSessionFactory()
    for fixture_name in (
        "private-w2-command-dispatch.json",
        "private-w2-direct-source-registration-dispatch.json",
    ):
        queue = FakeQueue([_delivery(fixture_name)])

        result = _consume(queue, session_factory, handler, mode="mixed")

        assert result.status == "APPLIED"
        assert queue.deleted == [f"receipt-{fixture_name}"]

    assert len(handler.dispatches) == 2
    assert session_factory.calls == 0


def test_mixed_router_routes_prepare_and_finalize_to_injected_gate_applier_once() -> None:
    """Replacing the injected collection-aware applier with the CT15 default must fail."""

    collection_handler = UnexpectedCallable()
    gate_applier = RecordingGateApplier()
    session_factory = GateSessionFactory()
    for fixture_name in (
        "private-w2-commit-gate-prepare.json",
        "private-w2-commit-gate-finalize.json",
    ):
        queue = FakeQueue([_delivery(fixture_name)])

        result = _consume(
            queue,
            session_factory,
            collection_handler,
            mode="mixed",
            gate_applier=gate_applier,
        )

        assert result.status == "APPLIED"
        assert queue.deleted == [f"receipt-{fixture_name}"]

    assert collection_handler.calls == 0
    assert len(gate_applier.gates) == 2
    assert session_factory.begin_calls == 2


def test_dedicated_modes_retain_wrong_message_without_calling_handler_or_gate() -> None:
    """Deleting a message owned by the other dedicated consumer must fail."""

    collection_handler = RecordingCollectionHandler()
    gate_applier = UnexpectedCallable()
    collection_queue = FakeQueue([_delivery("private-w2-commit-gate-prepare.json")])
    gate_queue = FakeQueue([_delivery("private-w2-command-dispatch.json")])

    collection_result = _consume(
        collection_queue,
        NeverSessionFactory(),
        collection_handler,
        mode="collection",
        gate_applier=gate_applier,
    )
    gate_result = _consume(
        gate_queue,
        NeverSessionFactory(),
        collection_handler,
        mode="gate",
        gate_applier=gate_applier,
    )

    assert collection_result.status == "REJECTED"
    assert gate_result.status == "REJECTED"
    assert collection_queue.deleted == []
    assert gate_queue.deleted == []
    assert collection_handler.dispatches == []
    assert gate_applier.calls == 0


def test_sender_rejection_happens_before_json_decode_session_or_handler() -> None:
    """Moving sender verification below strict decode must fail this test."""

    handler = UnexpectedCallable()
    queue = FakeQueue(
        [
            QueueDelivery(
                receipt_handle="foreign-receipt",
                body="{not valid json",
                sender_id="foreign-sender:synthetic-session",
            )
        ]
    )
    session_factory = NeverSessionFactory()

    result = _consume(queue, session_factory, handler, mode="mixed")

    assert result.status == "UNAUTHENTICATED"
    assert queue.deleted == []
    assert handler.calls == 0
    assert session_factory.calls == 0


def test_malformed_collection_message_is_retained_without_db_or_handler_work() -> None:
    """Passing malformed source dispatches to a handler must fail this test."""

    handler = UnexpectedCallable()
    queue = FakeQueue(
        [
            QueueDelivery(
                receipt_handle="duplicate-key-receipt",
                body='{"message_type":"w1.private.w2.collection-command.v1","message_type":"duplicate"}',
                sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
            )
        ]
    )
    session_factory = NeverSessionFactory()

    result = _consume(queue, session_factory, handler, mode="mixed")

    assert result.status == "MALFORMED"
    assert queue.deleted == []
    assert handler.calls == 0
    assert session_factory.calls == 0


def test_collection_relay_authorizer_reconstructs_lookup_from_stored_command_only() -> None:
    """Using a non-durable W1 dispatch wrapper instead of staged command must fail."""

    payload, command = _staged_payload()
    original_payload = json.loads(json.dumps(payload, sort_keys=True))
    lookup = RecordingRelayLookup(_available_lookup(command))

    build_collection_relay_authorizer(lookup)("STAGED", payload)

    assert lookup.requests == [
        LookupRequest(
            schema_version="w1.private.command-lookup.v1",
            command_id=command.command_id,
            execution_fence=int(command.execution_fence),
            owner_deletion_epoch=command.owner_deletion_epoch,
        )
    ]
    assert payload == original_payload


@pytest.mark.parametrize(
    "status",
    ["NOT_FOUND", "STALE_FENCE", "STALE_DELETION_EPOCH", "DELETED", "INVALIDATED", "EXPIRED"],
)
def test_collection_relay_authorizer_rejects_every_unavailable_lookup_status(status: str) -> None:
    """Sending a staged payload after a tombstone or rejected lookup must fail."""

    payload, command = _staged_payload()
    lookup = RecordingRelayLookup(
        LookupResponse(
            schema_version="w1.private.command-lookup.v1",
            command_id=command.command_id,
            status=status,  # type: ignore[arg-type]
            reason_code=status,
            command=None,
        )
    )

    with pytest.raises(RuntimeAuthorizationError):
        build_collection_relay_authorizer(lookup)("STAGED", payload)

    assert len(lookup.requests) == 1


def test_collection_relay_authorizer_rejects_available_response_with_different_command() -> None:
    """Comparing only lookup status rather than the full returned command must fail."""

    payload, command = _staged_payload()
    mismatched_command = command.model_copy(
        update={"purpose_ref": f"mismatch-{command.purpose_ref}"}
    )
    lookup = RecordingRelayLookup(_available_lookup(mismatched_command))

    with pytest.raises(RuntimeAuthorizationError):
        build_collection_relay_authorizer(lookup)("STAGED", payload)

    assert len(lookup.requests) == 1


def test_collection_relay_authorizer_rejects_malformed_stage_before_lookup() -> None:
    """Calling W1 for a malformed durable stage rather than failing closed must fail."""

    lookup = NeverRelayLookup()

    with pytest.raises(RuntimeAuthorizationError):
        build_collection_relay_authorizer(lookup)("STAGED", {"command": {}})

    assert lookup.calls == 0


def test_ack_relay_authorizer_does_not_call_collection_lookup() -> None:
    """Adding a collection lookup requirement to committed ACK relay must fail."""

    lookup = NeverRelayLookup()

    build_collection_relay_authorizer(lookup)("ACK", {})

    assert lookup.calls == 0


@pytest.mark.parametrize("mode", ["mixed", "collection"])
def test_collection_visibility_heartbeat_repeats_then_stops_before_receipt_delete(
    mode: str,
) -> None:
    """Replacing periodic lease-bound extension with one pre-handler call must fail."""

    queue = HeartbeatQueue([_delivery("private-w2-command-dispatch.json")])

    def wait_for_second_extension(_dispatch: object) -> None:
        assert queue.second_extension.wait(timeout=2)
        queue.handler_finished.set()

    result = _consume(
        queue,
        NeverSessionFactory(),
        wait_for_second_extension,
        mode=mode,
        visibility_heartbeat_seconds=0.01,
    )

    assert result.status == "APPLIED"
    assert len(queue.visibility_receipts) >= 2
    assert queue.visibility_receipts[0] == "receipt-private-w2-command-dispatch.json"
    assert queue.deleted == ["receipt-private-w2-command-dispatch.json"]
    assert queue.delete_after_handler is True
    assert not queue.extension_after_delete.wait(timeout=0.1)


def test_initial_visibility_extension_failure_prevents_handler_and_receipt_delete() -> None:
    """Starting collection work without the first full visibility lease must fail."""

    queue = HeartbeatQueue(
        [_delivery("private-w2-command-dispatch.json")],
        fail_on_extension=1,
    )
    handler = UnexpectedCallable()

    result = _consume(
        queue,
        NeverSessionFactory(),
        handler,
        mode="mixed",
        visibility_heartbeat_seconds=0.01,
    )

    assert result.status == "REJECTED"
    assert queue.visibility_receipts == ["receipt-private-w2-command-dispatch.json"]
    assert handler.calls == 0
    assert queue.deleted == []


def test_periodic_visibility_loss_leaves_completed_handler_receipt_undeleted() -> None:
    """Deleting after a lost periodic receipt lease must fail closed."""

    queue = HeartbeatQueue(
        [_delivery("private-w2-command-dispatch.json")],
        fail_on_extension=2,
    )
    handler_calls = 0

    def wait_for_visibility_loss(_dispatch: object) -> None:
        nonlocal handler_calls
        handler_calls += 1
        assert queue.extension_failed.wait(timeout=2)
        queue.handler_finished.set()

    result = _consume(
        queue,
        NeverSessionFactory(),
        wait_for_visibility_loss,
        mode="mixed",
        visibility_heartbeat_seconds=0.01,
    )

    assert result.status == "REJECTED"
    assert handler_calls == 1
    assert len(queue.visibility_receipts) >= 2
    assert queue.deleted == []
