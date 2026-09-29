"""Durable private commit-gate delivery against approved PostgreSQL."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateCommand,
    parse_commit_gate_command,
    staged_result_digest,
)
from epick_engine.source_collection.commit_gate_runtime import (
    QueueDelivery,
)
from epick_engine.source_collection.commit_gate_runtime import (
    consume_once as _consume_once,
)
from epick_engine.source_collection.commit_gate_runtime import (
    relay_once as _relay_once,
)
from epick_engine.source_collection.commit_gate_store import (
    CommitGateRejected,
    PrivateCommitGateAck,
    PrivateCommitGateInbox,
    PrivateCommitGateReceipt,
    PrivateCommitStage,
    PrivateStagedOutbox,
    cleanup_terminal_staged_outbox,
    lock_private_command,
)
from epick_engine.source_collection.commit_gate_store import (
    apply_commit_gate as _apply_commit_gate,
)
from epick_engine.source_collection.commit_gate_store import (
    stage_private_result as _stage_private_result,
)
from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult
from epick_engine.source_collection.ct15_transport_controls import ControlledQueue
from epick_engine.source_collection.persistence import Base, PrivateDeletionOwnerState
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateGateAuthority,
    PrivateScopeRejected,
    PrivateTerminalCleanupAuthority,
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
)
from epick_engine.source_collection.source_runtime import build_collection_relay_authorizer
from epick_engine.source_collection.w1_lookup_client import W1LookupClientError
from epick_engine.source_collection.w1_private_authority_contracts import (
    GateAuthorityResponse,
    GateScopeLookupResponse,
    PrivateWriteAuthorityResponse,
    TerminalCleanupAuthorityResponse,
    W1GateBinding,
    W1PrivateBinding,
)
from epick_engine.source_collection.w1_transport import LookupRequest, LookupResponse

pytestmark = pytest.mark.approved_postgres
NOW = datetime(2026, 9, 19, 1, 2, 3, tzinfo=UTC)
EXPECTED_SENDER_ID = "AROASYNTHETICROLE01"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    admin = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_w2_gate_delivery_{uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = admin.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(database_engine, expire_on_commit=False)


@dataclass
class FakeQueue:
    deliveries: list[QueueDelivery] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    fail_send: bool = False
    fail_delete: bool = False
    on_delete: Callable[[], None] | None = None

    def receive(self) -> list[QueueDelivery]:
        return list(self.deliveries)

    def delete(self, receipt_handle: str) -> None:
        if self.on_delete is not None:
            self.on_delete()
        if self.fail_delete:
            raise RuntimeError("synthetic delete failure")
        self.deleted.append(receipt_handle)

    def send(self, body: str) -> None:
        self.sent.append(body)
        if self.fail_send:
            raise RuntimeError("synthetic send failure")


@dataclass
class BlockingSendQueue(FakeQueue):
    send_started: Event = field(default_factory=Event)
    release_send: Event = field(default_factory=Event)

    def send(self, body: str) -> None:
        self.sent.append(body)
        self.send_started.set()
        if not self.release_send.wait(timeout=5):
            raise RuntimeError("synthetic blocked send timed out")


class RecordingRelayLookup:
    def __init__(self, response: LookupResponse) -> None:
        self._response = response
        self.requests: list[LookupRequest] = []

    def lookup(self, request: LookupRequest) -> LookupResponse:
        self.requests.append(request)
        return self._response


def _pair() -> tuple[CollectionCommand, CollectionResult]:
    raw = json.loads(
        (FIXTURES / "w2_commit_gate_proposal/digest-vector.json").read_text(encoding="utf-8")
    )
    command_id, job_id, owner_ref = uuid4(), uuid4(), uuid4()
    raw["command"].update(
        command_id=str(command_id), job_id=str(job_id), authenticated_owner_ref=str(owner_ref)
    )
    raw["result"].update(command_id=str(command_id), job_id=str(job_id))
    return CollectionCommand.model_validate(raw["command"]), CollectionResult.model_validate(
        raw["result"]
    )


def _gate(
    command: CollectionCommand,
    result: CollectionResult,
    action: str,
    *,
    operation_id: UUID,
    revision: int,
) -> CommitGateCommand:
    raw = json.loads(
        (FIXTURES / f"w1_private_contract/private-w2-commit-gate-{action.lower()}.json").read_text(
            encoding="utf-8"
        )
    )
    raw.update(
        message_id=str(uuid4()),
        operation_id=str(operation_id),
        operation_revision=revision,
        command_id=str(command.command_id),
        job_id=str(command.job_id),
        authenticated_owner_ref=str(command.authenticated_owner_ref),
        execution_fence=int(command.execution_fence),
        owner_deletion_epoch=command.owner_deletion_epoch,
        result_digest=staged_result_digest(command, result),
    )
    if action == "PURGE":
        raw["purge_owner_deletion_epoch"] = command.owner_deletion_epoch + 1
    return parse_commit_gate_command(raw)


def _command_scope(command: CollectionCommand) -> PrivateWriteScope:
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=command.authenticated_owner_ref,
            owner_deletion_epoch=command.owner_deletion_epoch,
            scope=PrivateDeletionScope(kind="ACCOUNT", project_id=None),
            authority_ref="w1:test-delivery-authority",
            command_id=command.command_id,
            job_id=command.job_id,
        )
    )


def _terminal_cleanup_authority(
    command: CollectionCommand,
    *,
    cleanup_kind: str = "STAGED_OUTBOX",
    **overrides: object,
) -> PrivateTerminalCleanupAuthority:
    binding = W1PrivateBinding.from_collection(command)
    response = TerminalCleanupAuthorityResponse.model_validate(
        {
            "schema_version": "w1.private.w2-terminal-cleanup.v1",
            "owner_user_id": binding.owner_user_id,
            "owner_deletion_epoch": binding.owner_deletion_epoch,
            "command_id": binding.command_id,
            "job_id": binding.job_id,
            "execution_fence": binding.execution_fence,
            "scope": {"type": "ACCOUNT"},
            "cleanup_kind": cleanup_kind,
            "authority_ref": "w1:test-terminal-cleanup",
            "allowed_effect": "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY",
            **overrides,
        }
    )
    return PrivateTerminalCleanupAuthority.from_w1_response(response)


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


def _gate_authority(gate: CommitGateCommand) -> PrivateGateAuthority:
    binding = W1GateBinding.from_gate(gate)
    response = GateAuthorityResponse.model_validate(
        {
            "schema_version": "w1.private.w2-gate-authority.v1",
            **_gate_payload(binding, "APPLY"),
            "scope": {"type": "ACCOUNT"},
            "authority_ref": "w1:test-delivery-gate-authority",
        }
    )
    return PrivateGateAuthority.from_w1_response(response)


class _GateAuthorityClient:
    def __init__(
        self,
        *,
        gate_scope: PrivateDeletionScope | None = None,
        expected_gate: W1GateBinding | None = None,
        lookup_error: Exception | None = None,
        authorize_gate_error: Exception | None = None,
        authorize_write_error: Exception | None = None,
    ) -> None:
        self.gate_scope = gate_scope or PrivateDeletionScope(kind="ACCOUNT", project_id=None)
        self.expected_gate = expected_gate
        self.lookup_error = lookup_error
        self.authorize_gate_error = authorize_gate_error
        self.authorize_write_error = authorize_write_error
        self.lookup_calls: list[tuple[W1GateBinding, str]] = []
        self.gate_authorize_calls: list[tuple[W1GateBinding, str, PrivateDeletionScope]] = []
        self.write_authorize_calls: list[tuple[W1PrivateBinding, PrivateDeletionScope]] = []
        self.cleanup_calls: list[tuple[W1PrivateBinding, PrivateDeletionScope, str]] = []

    def lookup_gate_scope(
        self,
        gate: W1GateBinding,
        phase: str,
    ) -> GateScopeLookupResponse:
        self.lookup_calls.append((gate, phase))
        if self.lookup_error is not None:
            raise self.lookup_error
        if self.expected_gate is not None and gate != self.expected_gate:
            raise W1LookupClientError("HTTP_403")
        return GateScopeLookupResponse.model_validate(
            {
                "schema_version": "w1.private.w2-gate-scope-lookup.v1",
                **_gate_payload(gate, phase),
                "scope": self.gate_scope.to_mapping(),
            }
        )

    def authorize_gate(
        self,
        gate: W1GateBinding,
        phase: str,
        scope: PrivateDeletionScope,
    ) -> GateAuthorityResponse:
        self.gate_authorize_calls.append((gate, phase, scope))
        if self.authorize_gate_error is not None:
            raise self.authorize_gate_error
        if self.expected_gate is not None and gate != self.expected_gate:
            raise W1LookupClientError("HTTP_403")
        return GateAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-gate-authority.v1",
                **_gate_payload(gate, phase),
                "scope": scope.to_mapping(),
                "authority_ref": "w1:test-delivery-gate-authority",
            }
        )

    def authorize_write(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
    ) -> PrivateWriteAuthorityResponse:
        self.write_authorize_calls.append((binding, scope))
        if self.authorize_write_error is not None:
            raise self.authorize_write_error
        return PrivateWriteAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-write-authority.v1",
                "owner_user_id": binding.owner_user_id,
                "owner_deletion_epoch": binding.owner_deletion_epoch,
                "command_id": binding.command_id,
                "job_id": binding.job_id,
                "execution_fence": binding.execution_fence,
                "scope": scope.to_mapping(),
                "authority_ref": "w1:test-delivery-write-authority",
            }
        )

    def authorize_terminal_cleanup(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
        cleanup_kind: str,
    ) -> TerminalCleanupAuthorityResponse:
        self.cleanup_calls.append((binding, scope, cleanup_kind))
        return TerminalCleanupAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-terminal-cleanup.v1",
                "owner_user_id": binding.owner_user_id,
                "owner_deletion_epoch": binding.owner_deletion_epoch,
                "command_id": binding.command_id,
                "job_id": binding.job_id,
                "execution_fence": binding.execution_fence,
                "scope": scope.to_mapping(),
                "cleanup_kind": cleanup_kind,
                "authority_ref": "w1:test-delivery-terminal-cleanup",
                "allowed_effect": "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY",
            }
        )


def stage_private_result(session, command, result, **kwargs):
    kwargs.setdefault("private_scope", _command_scope(command))
    return _stage_private_result(session, command, result, **kwargs)


def apply_commit_gate(session, gate, **kwargs):
    kwargs.setdefault("private_gate_authority", _gate_authority(gate))
    return _apply_commit_gate(session, gate, **kwargs)


def consume_once(session_factory, queue, expected_sender_id, **kwargs):
    kwargs.setdefault("private_authority_client", _GateAuthorityClient())
    return _consume_once(session_factory, queue, expected_sender_id, **kwargs)


def relay_once(session_factory, queue, **kwargs):
    kwargs.setdefault("authority_client", _GateAuthorityClient())
    return _relay_once(session_factory, queue, **kwargs)


def _delivery(gate: CommitGateCommand, receipt: str = "synthetic-receipt") -> QueueDelivery:
    return QueueDelivery(
        receipt_handle=receipt,
        body=json.dumps(
            gate.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
        sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
    )


def _stage(
    session_factory: sessionmaker[Session],
    command: CollectionCommand,
    result: CollectionResult,
    *,
    message_id: UUID | None = None,
) -> None:
    with session_factory.begin() as session:
        stage_private_result(
            session, command, result, message_id=message_id or uuid4(), occurred_at=NOW
        )


def test_successful_commit_is_visible_before_receipt_delete(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    ack_message_id = uuid4()

    def assert_committed_before_delete() -> None:
        with session_factory() as session:
            inbox = session.get(PrivateCommitGateInbox, gate.message_id)
            assert inbox is not None
            assert inbox.ack_message_id == ack_message_id
            assert session.get(PrivateCommitGateAck, ack_message_id) is not None
            assert session.get(PrivateCommitStage, command.command_id).state == "PREPARED"

    queue = FakeQueue(deliveries=[_delivery(gate)], on_delete=assert_committed_before_delete)

    outcome = consume_once(
        session_factory,
        queue,
        EXPECTED_SENDER_ID,
        clock=lambda: NOW,
        message_id_factory=lambda: ack_message_id,
    )

    assert outcome.status == "APPLIED"
    assert queue.deleted == ["synthetic-receipt"]


def test_delete_failure_redelivery_reuses_exact_applied_ack(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    delivery = _delivery(gate)
    persisted_ack_id = uuid4()
    first = FakeQueue(deliveries=[delivery], fail_delete=True)

    first_outcome = consume_once(
        session_factory,
        first,
        EXPECTED_SENDER_ID,
        clock=lambda: NOW,
        message_id_factory=lambda: persisted_ack_id,
    )
    with session_factory() as session:
        first_payload = session.get(PrivateCommitGateAck, persisted_ack_id).payload

    second = FakeQueue(deliveries=[delivery])
    second_outcome = consume_once(
        session_factory,
        second,
        EXPECTED_SENDER_ID,
        clock=lambda: NOW + timedelta(minutes=5),
        message_id_factory=uuid4,
    )

    assert first_outcome.status == "DELETE_FAILED"
    assert first.deleted == []
    assert second_outcome.status == "APPLIED"
    assert second.deleted == ["synthetic-receipt"]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(PrivateCommitGateAck)) == 1
        inbox = session.get(PrivateCommitGateInbox, gate.message_id)
        assert inbox.ack_message_id == persisted_ack_id
        assert session.get(PrivateCommitGateAck, persisted_ack_id).payload == first_payload
        assert first_payload["outcome"] == "APPLIED"


def test_accepted_gate_replay_rearms_and_relays_the_exact_stored_ack(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    delivery = _delivery(gate)
    inbound = FakeQueue(deliveries=[delivery])

    assert (
        consume_once(
            session_factory,
            inbound,
            EXPECTED_SENDER_ID,
            clock=lambda: NOW,
            message_id_factory=uuid4,
        ).status
        == "APPLIED"
    )
    outbound = FakeQueue()
    assert relay_once(session_factory, outbound, clock=lambda: NOW).status == "SENT"
    assert relay_once(session_factory, outbound, clock=lambda: NOW).status == "SENT"
    first_ack_body = outbound.sent[1]
    with session_factory() as session:
        ack = session.scalar(select(PrivateCommitGateAck))
        assert ack.delivered_at == NOW

    assert (
        consume_once(
            session_factory,
            inbound,
            EXPECTED_SENDER_ID,
            clock=lambda: NOW + timedelta(minutes=1),
            message_id_factory=uuid4,
        ).status
        == "APPLIED"
    )
    with session_factory() as session:
        ack = session.scalar(select(PrivateCommitGateAck))
        assert ack.delivered_at is None

    assert (
        relay_once(session_factory, outbound, clock=lambda: NOW + timedelta(minutes=2)).status
        == "SENT"
    )
    assert outbound.sent[2] == first_ack_body
    assert json.loads(outbound.sent[2])["outcome"] == "APPLIED"
    with session_factory() as session:
        ack = session.scalar(select(PrivateCommitGateAck))
        assert ack.delivered_at == NOW + timedelta(minutes=2)


def test_stale_gate_is_rejected_without_receipt_delete(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    operation_id = uuid4()
    with session_factory.begin() as session:
        apply_commit_gate(
            session,
            _gate(command, result, "PREPARE", operation_id=operation_id, revision=2),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
    stale = _gate(command, result, "FINALIZE", operation_id=operation_id, revision=1)
    queue = FakeQueue(deliveries=[_delivery(stale)])

    outcome = consume_once(session_factory, queue, EXPECTED_SENDER_ID)

    assert outcome.status == "REJECTED"
    assert queue.deleted == []


def test_send_failure_leaves_staged_delivery_unmarked(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    queue = FakeQueue(fail_send=True)

    outcome = relay_once(session_factory, queue, clock=lambda: NOW)

    assert outcome.status == "SEND_FAILED"
    assert len(queue.sent) == 1
    with session_factory() as session:
        row = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert row.delivered_at is None


def test_send_failure_clears_exact_relay_claim_and_allows_immediate_same_wire_retry(
    session_factory,
) -> None:
    """Leaving a failed relay claim active would incorrectly delay a safe retry."""

    command, result = _pair()
    _stage(session_factory, command, result)
    first_token, second_token = uuid4(), uuid4()
    failed_queue = FakeQueue(fail_send=True)
    failed_authority = _GateAuthorityClient()

    first = relay_once(
        session_factory,
        failed_queue,
        authority_client=failed_authority,
        clock=lambda: NOW,
        claim_token_factory=lambda: first_token,
        claim_lease_seconds=120,
    )

    assert first.status == "SEND_FAILED"
    assert len(failed_authority.write_authorize_calls) == 3
    with session_factory() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        expected_payload = staged.payload
        assert staged.delivered_at is None
        assert staged.relay_claim_token is None
        assert staged.relay_claim_expires_at is None

    recovered_queue = FakeQueue()
    second = relay_once(
        session_factory,
        recovered_queue,
        clock=lambda: NOW + timedelta(seconds=1),
        claim_token_factory=lambda: second_token,
        claim_lease_seconds=120,
    )

    assert second.status == "SENT", f"second send attempts: {len(recovered_queue.sent)}"
    assert len(recovered_queue.sent) == 1
    assert json.loads(recovered_queue.sent[0]) == expected_payload
    with session_factory() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert staged.delivered_at == NOW + timedelta(seconds=1)
        assert staged.relay_claim_token is None
        assert staged.relay_claim_expires_at is None


def test_active_relay_claim_blocks_terminal_then_expired_claim_is_reclaimed(
    session_factory,
) -> None:
    """Terminal mutation must reject an active claim but may clear an expired DB-clock claim."""

    command, result = _pair()
    _stage(session_factory, command, result)
    purge = _gate(command, result, "PURGE", operation_id=uuid4(), revision=1)
    active_token = uuid4()
    with session_factory.begin() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        db_now = session.scalar(select(func.clock_timestamp()))
        staged.relay_claim_token = active_token
        staged.relay_claim_expires_at = db_now + timedelta(minutes=1)

    assert relay_once(session_factory, FakeQueue(), clock=lambda: NOW).status == "EMPTY"
    with pytest.raises(CommitGateRejected):
        with session_factory.begin() as session:
            apply_commit_gate(
                session,
                purge,
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
    with session_factory() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert stage.state != "PURGED"
        assert staged.payload is not None
        assert staged.relay_claim_token == active_token

    with session_factory.begin() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        db_now = session.scalar(select(func.clock_timestamp()))
        staged.relay_claim_expires_at = db_now - timedelta(seconds=1)

    queue = FakeQueue()
    assert relay_once(session_factory, queue, clock=lambda: NOW).status == "SENT"
    assert len(queue.sent) == 1
    with session_factory() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert staged.delivered_at == NOW
        assert staged.relay_claim_token is None
        assert staged.relay_claim_expires_at is None


@pytest.mark.parametrize("action", ["ABORT", "PURGE"])
def test_terminal_gate_waits_for_owner_fenced_relay_before_mutating(
    session_factory,
    action: str,
) -> None:
    """A terminal gate cannot commit while a private send holds the owner fence."""

    command, result = _pair()
    _stage(session_factory, command, result)
    gate = _gate(command, result, action, operation_id=uuid4(), revision=1)
    queue = BlockingSendQueue()
    terminal_started = Event()

    def apply_terminal() -> None:
        terminal_started.set()
        with session_factory.begin() as session:
            apply_commit_gate(
                session,
                gate,
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        relay_future = pool.submit(relay_once, session_factory, queue, clock=lambda: NOW)
        assert queue.send_started.wait(timeout=5)
        terminal_future = pool.submit(apply_terminal)
        assert terminal_started.wait(timeout=5)
        assert not terminal_future.done()
        queue.release_send.set()
        assert relay_future.result(timeout=5).status == "SENT"
        terminal_future.result(timeout=5)

    with session_factory() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert stage.state == ("ABORTED" if action == "ABORT" else "PURGED")
        assert stage.result_payload is None
        assert staged.payload is None
        assert staged.delivered_at == NOW


def test_old_failed_relay_cleanup_cannot_clear_a_newer_claim_token(session_factory) -> None:
    """A stale worker clearing another relay's claim after failure must fail this test."""

    command, result = _pair()
    _stage(session_factory, command, result)
    old_token, new_token = uuid4(), uuid4()

    def replace_claim_then_fail(_kind: str, _payload: object) -> None:
        with session_factory.begin() as session:
            lock_private_command(session, command.command_id)
            staged = session.scalar(
                select(PrivateStagedOutbox).where(
                    PrivateStagedOutbox.command_id == command.command_id
                )
            )
            db_now = session.scalar(select(func.clock_timestamp()))
            staged.relay_claim_token = new_token
            staged.relay_claim_expires_at = db_now + timedelta(minutes=1)
        raise RuntimeError("synthetic stale relay failure")

    outcome = relay_once(
        session_factory,
        FakeQueue(),
        clock=lambda: NOW,
        before_send=replace_claim_then_fail,
        claim_token_factory=lambda: old_token,
        claim_lease_seconds=120,
    )

    assert outcome.status == "SEND_FAILED"
    with session_factory() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert staged.delivered_at is None
        assert staged.relay_claim_token == new_token
        assert staged.relay_claim_expires_at is not None


def test_send_commit_failure_retries_same_body_and_id(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    queue = FakeQueue()
    armed = True

    def fail_first_commit_after_send(_session: Session) -> None:
        nonlocal armed
        if armed and queue.sent:
            armed = False
            raise RuntimeError("synthetic post-send commit failure")

    event.listen(session_factory.class_, "before_commit", fail_first_commit_after_send)
    try:
        first = relay_once(session_factory, queue, clock=lambda: NOW)
    finally:
        event.remove(session_factory.class_, "before_commit", fail_first_commit_after_send)

    assert first.status == "SEND_FAILED"
    with session_factory() as session:
        row = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert row.delivered_at is None

    second = relay_once(session_factory, queue, clock=lambda: NOW + timedelta(seconds=1))

    assert second.status == "SENT"
    assert len(queue.sent) == 2
    assert queue.sent[0] == queue.sent[1]
    assert json.loads(queue.sent[0])["message_id"] == json.loads(queue.sent[1])["message_id"]
    with session_factory() as session:
        row = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert row.delivered_at == NOW + timedelta(seconds=1)


def test_before_send_rejection_keeps_staged_payload_undelivered_and_immutable(
    session_factory,
) -> None:
    """Moving the source-runtime authorization callback after send must fail this test."""

    command, result = _pair()
    _stage(session_factory, command, result)
    with session_factory() as session:
        row = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        expected_payload = json.loads(json.dumps(row.payload, sort_keys=True))

    callback_calls = 0

    def reject_before_send(*_args: object) -> None:
        nonlocal callback_calls
        callback_calls += 1
        raise RuntimeError("synthetic source-runtime authorization rejection")

    queue = FakeQueue()
    outcome = relay_once(
        session_factory,
        queue,
        clock=lambda: NOW,
        before_send=reject_before_send,
    )

    assert outcome.status == "SEND_FAILED"
    assert callback_calls == 1
    assert queue.sent == []
    with session_factory() as session:
        row = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert row.delivered_at is None
        assert row.payload == expected_payload


def test_before_send_authorization_runs_without_holding_the_command_lock(
    session_factory,
) -> None:
    """Holding the command lock during W1 lookup must fail this bounded probe."""

    command, result = _pair()
    _stage(session_factory, command, result)
    queue = FakeQueue()
    probe_completed = False

    def prove_no_command_lock(kind: str, _payload: object) -> None:
        nonlocal probe_completed
        assert kind == "STAGED"
        with session_factory.begin() as verifier:
            verifier.execute(text("SET LOCAL statement_timeout = '200ms'"))
            lock_private_command(verifier, command.command_id)
            row = verifier.scalar(
                select(PrivateStagedOutbox)
                .where(PrivateStagedOutbox.command_id == command.command_id)
                .with_for_update(nowait=True)
            )
            assert row is not None
            probe_completed = True

    outcome = relay_once(
        session_factory,
        queue,
        clock=lambda: NOW,
        before_send=prove_no_command_lock,
    )

    assert outcome.status == "SENT"
    assert probe_completed is True
    assert len(queue.sent) == 1


def test_queue_send_runs_without_holding_the_command_lock(session_factory) -> None:
    """Holding the command lock through network send must fail this bounded probe."""

    command, result = _pair()
    _stage(session_factory, command, result)
    probe_completed = False

    class LockProbeQueue(FakeQueue):
        def send(self, body: str) -> None:
            nonlocal probe_completed
            with session_factory.begin() as verifier:
                verifier.execute(text("SET LOCAL statement_timeout = '200ms'"))
                lock_private_command(verifier, command.command_id)
                row = verifier.scalar(
                    select(PrivateStagedOutbox)
                    .where(PrivateStagedOutbox.command_id == command.command_id)
                    .with_for_update(nowait=True)
                )
                assert row is not None
                probe_completed = True
            super().send(body)

    queue = LockProbeQueue()
    outcome = relay_once(session_factory, queue, clock=lambda: NOW)

    assert outcome.status == "SENT"
    assert probe_completed is True
    assert len(queue.sent) == 1


def test_ack_relay_requires_no_collection_lookup_dependency(session_factory) -> None:
    """Adding a collection lookup prerequisite to committed ACK relay must fail this test."""

    command, result = _pair()
    _stage(session_factory, command, result)
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    inbound = FakeQueue(deliveries=[_delivery(gate)])
    assert (
        consume_once(
            session_factory,
            inbound,
            EXPECTED_SENDER_ID,
            clock=lambda: NOW,
            message_id_factory=uuid4,
        ).status
        == "APPLIED"
    )

    outbound = FakeQueue()
    assert relay_once(session_factory, outbound, clock=lambda: NOW).status == "SENT"
    callback_kinds: list[str] = []

    def record_before_send(kind: str, _payload: object) -> None:
        callback_kinds.append(kind)

    outcome = relay_once(
        session_factory,
        outbound,
        clock=lambda: NOW + timedelta(seconds=1),
        before_send=record_before_send,
    )

    assert outcome.status == "SENT"
    assert callback_kinds == ["ACK"]
    assert json.loads(outbound.sent[-1])["outcome"] == "APPLIED"


@pytest.mark.parametrize(
    "status",
    ["NOT_FOUND", "STALE_FENCE", "STALE_DELETION_EPOCH", "DELETED", "INVALIDATED", "EXPIRED"],
)
def test_collection_lookup_rejection_leaves_staged_delivery_unsent_and_replayable(
    session_factory,
    status: str,
) -> None:
    """Sending or marking a staged result after an unavailable lookup must fail."""

    command, result = _pair()
    _stage(session_factory, command, result)
    lookup = RecordingRelayLookup(
        LookupResponse(
            schema_version="w1.private.command-lookup.v1",
            command_id=command.command_id,
            status=status,  # type: ignore[arg-type]
            reason_code=status,
            command=None,
        )
    )
    queue = FakeQueue()

    outcome = relay_once(
        session_factory,
        queue,
        clock=lambda: NOW,
        before_send=build_collection_relay_authorizer(lookup),
    )

    assert outcome.status == "SEND_FAILED"
    assert len(lookup.requests) == 1
    assert queue.sent == []
    assert queue.deleted == []
    with session_factory() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert staged.delivered_at is None
        assert staged.relay_claim_token is None
        assert staged.relay_claim_expires_at is None


def test_collection_lookup_full_command_mismatch_leaves_staged_delivery_unsent(
    session_factory,
) -> None:
    """Comparing only AVAILABLE status instead of the complete command must fail."""

    command, result = _pair()
    _stage(session_factory, command, result)
    mismatched_command = command.model_copy(
        update={"purpose_ref": f"mismatch-{command.purpose_ref}"}
    )
    lookup = RecordingRelayLookup(
        LookupResponse(
            schema_version="w1.private.command-lookup.v1",
            command_id=command.command_id,
            status="AVAILABLE",
            reason_code=None,
            command=mismatched_command,
        )
    )
    queue = FakeQueue()

    outcome = relay_once(
        session_factory,
        queue,
        clock=lambda: NOW,
        before_send=build_collection_relay_authorizer(lookup),
    )

    assert outcome.status == "SEND_FAILED"
    assert len(lookup.requests) == 1
    assert queue.sent == []
    assert queue.deleted == []
    with session_factory() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert staged.delivered_at is None
        assert staged.relay_claim_token is None


def test_terminal_staged_cleanup_never_sends_or_creates_gate(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)

    with session_factory.begin() as session, pytest.raises(PrivateScopeRejected):
        cleanup_terminal_staged_outbox(
            session,
            command.command_id,
            private_cleanup_authority=_terminal_cleanup_authority(
                command,
                cleanup_kind="CLAIM_RELEASE",
            ),
        )

    binding = W1PrivateBinding.from_collection(command)
    malformed = TerminalCleanupAuthorityResponse.model_construct(
        schema_version="w1.private.w2-terminal-cleanup.v1",
        owner_user_id=binding.owner_user_id,
        owner_deletion_epoch=binding.owner_deletion_epoch,
        command_id=binding.command_id,
        job_id=binding.job_id,
        execution_fence=binding.execution_fence,
        scope={"type": "ACCOUNT"},
        cleanup_kind="STAGED_OUTBOX",
        authority_ref="w1:test-terminal-cleanup",
        allowed_effect="WRITE_TOO",
    )
    with pytest.raises(PrivateScopeRejected):
        PrivateTerminalCleanupAuthority.from_w1_response(malformed)

    with session_factory.begin() as session:
        cleanup_terminal_staged_outbox(
            session,
            command.command_id,
            private_cleanup_authority=_terminal_cleanup_authority(command),
        )

    queue = FakeQueue()
    assert relay_once(session_factory, queue, command_id=command.command_id).status == "EMPTY"
    assert queue.sent == []
    with session_factory() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert stage is not None
        assert stage.state == "STAGED"
        assert stage.payload_purged is True
        assert stage.result_payload is None
        assert stage.operation_id is None
        assert stage.operation_revision == "0"
        assert staged is not None
        assert staged.payload is None
        assert staged.delivered_at is None
        assert staged.relay_claim_token is None
        assert session.scalar(select(func.count()).select_from(PrivateCommitGateAck)) == 0
        assert session.scalar(select(func.count()).select_from(PrivateCommitGateReceipt)) == 0
        assert session.scalar(select(func.count()).select_from(PrivateCommitGateInbox)) == 0


@pytest.mark.parametrize("target_case", ["absent", "non-staged", "binding-mismatch"])
def test_terminal_staged_cleanup_rejects_inexact_target_without_mutation(
    session_factory: sessionmaker[Session],
    target_case: str,
) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    if target_case == "absent":
        with session_factory.begin() as session:
            staged = session.scalar(
                select(PrivateStagedOutbox).where(
                    PrivateStagedOutbox.command_id == command.command_id
                )
            )
            stage = session.get(PrivateCommitStage, command.command_id)
            assert staged is not None and stage is not None
            session.delete(staged)
            session.flush()
            session.delete(stage)
    elif target_case == "non-staged":
        gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
        with session_factory.begin() as session:
            apply_commit_gate(
                session,
                gate,
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )

    def snapshot() -> dict[str, object]:
        with session_factory() as session:
            stage = session.get(PrivateCommitStage, command.command_id)
            staged = session.scalar(
                select(PrivateStagedOutbox).where(
                    PrivateStagedOutbox.command_id == command.command_id
                )
            )
            return {
                "stage": None
                if stage is None
                else (
                    stage.state,
                    stage.payload_purged,
                    stage.result_payload,
                    stage.operation_id,
                    stage.operation_revision,
                    stage.result_digest,
                ),
                "outbox": None
                if staged is None
                else (
                    staged.message_id,
                    staged.payload,
                    staged.delivered_at,
                    staged.relay_claim_token,
                    staged.relay_claim_expires_at,
                ),
                "acks": session.scalar(select(func.count()).select_from(PrivateCommitGateAck)),
                "receipts": session.scalar(
                    select(func.count()).select_from(PrivateCommitGateReceipt)
                ),
                "inbox": session.scalar(select(func.count()).select_from(PrivateCommitGateInbox)),
            }

    before = snapshot()
    authority_overrides = {"job_id": uuid4()} if target_case == "binding-mismatch" else {}
    with (
        session_factory.begin() as session,
        pytest.raises((CommitGateRejected, PrivateScopeRejected)),
    ):
        cleanup_terminal_staged_outbox(
            session,
            command.command_id,
            private_cleanup_authority=_terminal_cleanup_authority(
                command,
                **authority_overrides,
            ),
        )

    assert snapshot() == before


def test_purge_before_relay_suppresses_staged_private_payload(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    operation_id = uuid4()
    with session_factory.begin() as session:
        apply_commit_gate(
            session,
            _gate(command, result, "PURGE", operation_id=operation_id, revision=1),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
    queue = FakeQueue()

    first = relay_once(session_factory, queue, clock=lambda: NOW)
    second = relay_once(session_factory, queue, clock=lambda: NOW)

    assert first.status == "SENT"
    assert second.status == "EMPTY"
    assert len(queue.sent) == 1
    sent = json.loads(queue.sent[0])
    assert sent["message_type"] == "w2.private.commit-gate-ack.proposal.v1"
    assert sent["action"] == "PURGE"
    assert sent["outcome"] == "APPLIED"
    assert "command" not in sent
    assert "result" not in sent
    with session_factory() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert staged.payload is None
        assert staged.delivered_at is None


def test_active_relay_holds_owner_fence_until_send_then_allows_purge(session_factory) -> None:
    """PURGE waits for the bounded send and commits only after send completion."""

    command, result = _pair()
    _stage(session_factory, command, result)
    purge = _gate(command, result, "PURGE", operation_id=uuid4(), revision=1)
    queue = BlockingSendQueue()
    purge_entered = Event()
    purge_done = Event()

    def apply_purge() -> Exception | None:
        purge_entered.set()
        try:
            with session_factory.begin() as session:
                apply_commit_gate(
                    session,
                    purge,
                    ack_message_id=uuid4(),
                    occurred_at=NOW,
                )
            return None
        except Exception as exc:
            return exc
        finally:
            purge_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        relay_future = pool.submit(relay_once, session_factory, queue, clock=lambda: NOW)
        assert queue.send_started.wait(timeout=5)
        purge_future = pool.submit(apply_purge)
        assert purge_entered.wait(timeout=5)
        try:
            assert not purge_done.wait(timeout=0.2)
        finally:
            queue.release_send.set()
        assert relay_future.result(timeout=5).status == "SENT"
        assert purge_future.result(timeout=5) is None

    with session_factory() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert stage.state == "PURGED"
        assert stage.result_payload is None
        assert staged.payload is None
        assert staged.delivered_at == NOW


def test_private_relay_cannot_send_after_competing_deletion_commits(session_factory) -> None:
    first_command, first_result = _pair()
    second_command, second_result = _pair()
    second_command = second_command.model_copy(
        update={"authenticated_owner_ref": first_command.authenticated_owner_ref}
    )
    _stage(session_factory, first_command, first_result)
    _stage(session_factory, second_command, second_result)
    queue = BlockingSendQueue()
    deletion_started = Event()

    def commit_deletion_tombstone() -> None:
        deletion_started.set()
        with session_factory.begin() as session:
            owner_state = session.scalar(
                select(PrivateDeletionOwnerState)
                .where(
                    PrivateDeletionOwnerState.owner_user_id == first_command.authenticated_owner_ref
                )
                .with_for_update()
            )
            assert owner_state is not None
            owner_state.latest_epoch = 1
            owner_state.account_deleted = True

    with ThreadPoolExecutor(max_workers=2) as pool:
        relay_future = pool.submit(
            relay_once,
            session_factory,
            queue,
            clock=lambda: NOW,
            command_id=first_command.command_id,
        )
        assert queue.send_started.wait(timeout=5)
        deletion_future = pool.submit(commit_deletion_tombstone)
        assert deletion_started.wait(timeout=5)
        assert not deletion_future.done()
        queue.release_send.set()
        assert relay_future.result(timeout=5).status == "SENT"
        deletion_future.result(timeout=5)

    after_deletion = FakeQueue()
    assert (
        relay_once(
            session_factory,
            after_deletion,
            command_id=second_command.command_id,
        ).status
        == "SEND_FAILED"
    )
    assert after_deletion.sent == []


def test_deleted_owner_ack_replays_original_wire(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    assert relay_once(session_factory, FakeQueue()).status == "SENT"
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    assert (
        consume_once(
            session_factory,
            FakeQueue(deliveries=[_delivery(gate)]),
            EXPECTED_SENDER_ID,
        ).status
        == "APPLIED"
    )
    with session_factory.begin() as session:
        owner = session.get(PrivateDeletionOwnerState, command.authenticated_owner_ref)
        stage = session.get(PrivateCommitStage, command.command_id)
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        ack = session.scalar(select(PrivateCommitGateAck))
        assert owner is not None and stage is not None and staged is not None and ack is not None
        original_id = ack.message_id
        original_body = json.dumps(
            ack.payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        owner.latest_epoch = command.owner_deletion_epoch + 1
        owner.account_deleted = True
        stage.payload_purged = True
        stage.result_payload = None
        staged.payload = None

    authority = _GateAuthorityClient(expected_gate=W1GateBinding.from_gate(gate))
    outbound = FakeQueue()
    outcome = relay_once(
        session_factory,
        outbound,
        authority_client=authority,
        command_id=command.command_id,
    )

    assert outcome.status == "SENT"
    assert outbound.sent == [original_body]
    assert json.loads(outbound.sent[0])["message_id"] == str(original_id)
    assert authority.write_authorize_calls == []
    assert [phase for _, phase, _ in authority.gate_authorize_calls] == [
        "ACK_RELAY",
        "ACK_RELAY",
    ]


@pytest.mark.parametrize(
    "gate_scope",
    [
        PrivateDeletionScope(kind="ACCOUNT", project_id=None),
        PrivateDeletionScope(
            kind="PROJECT", project_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        ),
    ],
    ids=["ACCOUNT", "PROJECT"],
)
def test_stage_less_ack_scope_lookup_before_relay(session_factory, gate_scope) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    assert relay_once(session_factory, FakeQueue()).status == "SENT"
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    assert (
        consume_once(
            session_factory,
            FakeQueue(deliveries=[_delivery(gate)]),
            EXPECTED_SENDER_ID,
        ).status
        == "APPLIED"
    )
    with session_factory.begin() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        assert stage is not None
        stage.private_scope_kind = "UNKNOWN"
        stage.project_id = None

    authority = _GateAuthorityClient(
        gate_scope=gate_scope,
        expected_gate=W1GateBinding.from_gate(gate),
    )
    outbound = FakeQueue()
    outcome = relay_once(
        session_factory,
        outbound,
        authority_client=authority,
        command_id=command.command_id,
    )

    assert outcome.status == "SENT"
    assert len(outbound.sent) == 1
    assert len(authority.lookup_calls) == 2
    assert [phase for _, phase in authority.lookup_calls] == ["ACK_RELAY", "ACK_RELAY"]
    assert [scope for _, _, scope in authority.gate_authorize_calls] == [
        gate_scope,
        gate_scope,
    ]


@pytest.mark.parametrize(
    "failure",
    [
        W1LookupClientError("HTTP_403"),
        W1LookupClientError("HTTP_503"),
        W1LookupClientError("TIMEOUT"),
    ],
    ids=["missing-historical-outbox-or-403", "503", "timeout"],
)
def test_stage_less_ack_scope_lookup_failure_does_not_probe_candidates(
    session_factory,
    failure,
) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    assert relay_once(session_factory, FakeQueue()).status == "SENT"
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    assert (
        consume_once(
            session_factory,
            FakeQueue(deliveries=[_delivery(gate)]),
            EXPECTED_SENDER_ID,
        ).status
        == "APPLIED"
    )
    with session_factory.begin() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        assert stage is not None
        stage.private_scope_kind = "UNKNOWN"
        stage.project_id = None

    authority = _GateAuthorityClient(lookup_error=failure)
    outbound = FakeQueue()
    outcome = relay_once(
        session_factory,
        outbound,
        authority_client=authority,
        command_id=command.command_id,
    )

    assert outcome.status == "SEND_FAILED"
    assert outbound.sent == []
    assert len(authority.lookup_calls) == 1
    assert authority.lookup_calls[0][1] == "ACK_RELAY"
    assert authority.gate_authorize_calls == []


def test_staged_send_race_with_deletion_is_owner_serialized(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    send_authorize_started = Event()
    release_send_authorize = Event()

    class BlockingSendAuthority(_GateAuthorityClient):
        def authorize_write(self, binding, scope):
            call_number = len(self.write_authorize_calls) + 1
            if call_number == 2:
                send_authorize_started.set()
                if not release_send_authorize.wait(timeout=5):
                    raise RuntimeError("synthetic send authorization timed out")
            if call_number >= 3:
                self.write_authorize_calls.append((binding, scope))
                raise W1LookupClientError("HTTP_403")
            return super().authorize_write(binding, scope)

    authority = BlockingSendAuthority()
    outbound = FakeQueue()

    with ThreadPoolExecutor(max_workers=2) as pool:
        relay_future = pool.submit(
            relay_once,
            session_factory,
            outbound,
            authority_client=authority,
            command_id=command.command_id,
        )
        assert send_authorize_started.wait(timeout=5)
        with session_factory.begin() as session:
            owner = session.get(PrivateDeletionOwnerState, command.authenticated_owner_ref)
            assert owner is not None
            owner.latest_epoch = command.owner_deletion_epoch + 1
            owner.account_deleted = True
        release_send_authorize.set()
        assert relay_future.result(timeout=5).status == "SEND_FAILED"

    assert outbound.sent == []
    assert len(authority.write_authorize_calls) == 3
    assert len(authority.cleanup_calls) == 1
    assert authority.cleanup_calls[0][2] == "STAGED_OUTBOX"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("occurred_at", "2026-09-19T01:02:04Z"),
        ("outcome", "DUPLICATE"),
    ],
)
def test_changed_ack_body_conflicts(session_factory, field, value) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    assert relay_once(session_factory, FakeQueue()).status == "SENT"
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    assert (
        consume_once(
            session_factory,
            FakeQueue(deliveries=[_delivery(gate)]),
            EXPECTED_SENDER_ID,
        ).status
        == "APPLIED"
    )
    with session_factory.begin() as session:
        ack = session.scalar(select(PrivateCommitGateAck))
        assert ack is not None
        ack.payload = {**ack.payload, field: value}

    authority = _GateAuthorityClient(expected_gate=W1GateBinding.from_gate(gate))
    outbound = FakeQueue()
    outcome = relay_once(
        session_factory,
        outbound,
        authority_client=authority,
        command_id=command.command_id,
    )

    assert outcome.status == "SEND_FAILED"
    assert outbound.sent == []
    assert authority.lookup_calls == []
    assert authority.write_authorize_calls == []
    assert authority.gate_authorize_calls == []
    with session_factory() as session:
        ack = session.scalar(select(PrivateCommitGateAck))
        assert ack is not None
        assert ack.delivered_at is None
        assert ack.relay_claim_token is None
        assert ack.relay_claim_expires_at is None


def test_staged_relay_still_runs_command_lookup(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    lookup = RecordingRelayLookup(
        LookupResponse(
            schema_version="w1.private.command-lookup.v1",
            command_id=command.command_id,
            status="AVAILABLE",
            reason_code=None,
            command=command,
        )
    )
    authority = _GateAuthorityClient()
    outbound = FakeQueue()

    outcome = relay_once(
        session_factory,
        outbound,
        authority_client=authority,
        command_id=command.command_id,
        before_send=build_collection_relay_authorizer(lookup),
    )

    assert outcome.status == "SENT"
    assert len(lookup.requests) == 1
    assert len(authority.write_authorize_calls) == 2
    assert len(outbound.sent) == 1


def test_relay_fails_closed_without_authority_client(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    outbound = FakeQueue()

    outcome = _relay_once(
        session_factory,
        outbound,
        command_id=command.command_id,
    )

    assert outcome.status == "SEND_FAILED"
    assert outbound.sent == []
    with session_factory() as session:
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert staged is not None
        assert staged.relay_claim_token is None
        assert staged.delivered_at is None


def test_denied_staged_relay_uses_only_terminal_cleanup(session_factory) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    authority = _GateAuthorityClient(authorize_write_error=W1LookupClientError("HTTP_403"))
    outbound = FakeQueue()

    outcome = relay_once(
        session_factory,
        outbound,
        authority_client=authority,
        command_id=command.command_id,
    )

    assert outcome.status == "SEND_FAILED"
    assert outbound.sent == []
    assert len(authority.write_authorize_calls) == 1
    assert len(authority.cleanup_calls) == 1
    assert authority.cleanup_calls[0][2] == "STAGED_OUTBOX"
    with session_factory() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        staged = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert stage is not None and staged is not None
        assert stage.payload_purged is True
        assert stage.result_payload is None
        assert staged.payload is None


def test_delivery_migration_precedes_the_forward_non_destructive_head() -> None:
    root = Path(__file__).resolve().parents[3]
    scripts = ScriptDirectory.from_config(Config(root / "alembic.ini"))
    revision = scripts.get_revision("0005_private_gate_delivery")
    restriction_revision = scripts.get_revision("0006_source_restriction")
    receipt_revision = scripts.get_revision("0007_restriction_receipt")
    runtime_revision = scripts.get_revision("0008_collection_runtime")
    deletion_revision = scripts.get_revision("0009_private_deletion_receipt")
    scope_v2_revision = scripts.get_revision("0010_private_deletion_scope_v2")
    ack_retention_revision = scripts.get_revision("0011_private_ack_control_retention")
    ack_wire_revision = scripts.get_revision("0012_private_ack_wire_digest")
    deletion_ack_revision = scripts.get_revision("0013_deletion_ack_confirmed")

    assert scripts.get_heads() == [deletion_ack_revision.revision]
    assert deletion_ack_revision.down_revision == ack_wire_revision.revision
    assert ack_wire_revision.down_revision == ack_retention_revision.revision
    assert ack_retention_revision.down_revision == scope_v2_revision.revision
    assert scope_v2_revision.down_revision == deletion_revision.revision
    assert deletion_revision.down_revision == runtime_revision.revision
    assert runtime_revision.down_revision == receipt_revision.revision
    assert receipt_revision.down_revision == restriction_revision.revision
    assert restriction_revision.down_revision == revision.revision
    assert revision.down_revision == "0004_private_commit_gate"
    assert Base.metadata.tables["private_staged_outbox"].c.delivered_at.nullable
    assert Base.metadata.tables["private_commit_gate_acks"].c.delivered_at.nullable
    with pytest.raises(RuntimeError, match="Destructive downgrade"):
        revision.module.downgrade()


@pytest.mark.parametrize("mode,initial_sends", [("drop-ack", 0), ("ack-send-uncertain", 1)])
def test_controlled_ack_loss_recovers_original_wire_with_new_relay(
    session_factory, mode, initial_sends
) -> None:
    command, result = _pair()
    _stage(session_factory, command, result)
    assert relay_once(session_factory, FakeQueue()).status == "SENT"
    incoming = FakeQueue(
        deliveries=[_delivery(_gate(command, result, "PREPARE", operation_id=uuid4(), revision=1))]
    )
    assert consume_once(session_factory, incoming, EXPECTED_SENDER_ID).status == "APPLIED"
    with session_factory() as session:
        ack = session.scalar(select(PrivateCommitGateAck))
        ack_id = ack.message_id
        original = ack.payload
    base = FakeQueue()
    failed_authority = _GateAuthorityClient()
    controlled = ControlledQueue(
        base,
        command=command,
        run_id="ct15-ack-recovery",
        mode=mode,
        approved=True,
        expected_sender_id=EXPECTED_SENDER_ID,
    )
    assert (
        relay_once(
            session_factory,
            controlled,
            authority_client=failed_authority,
        ).status
        == "SEND_FAILED"
    )
    assert len(failed_authority.gate_authorize_calls) == 3
    assert len(base.sent) == initial_sends
    with session_factory() as session:
        ack = session.get(PrivateCommitGateAck, ack_id)
        assert ack.delivered_at is None
        assert ack.payload == original

    # A fresh session factory/queue represents a new invocation, not a new ACK.
    restarted = sessionmaker(session_factory.kw["bind"], expire_on_commit=False)
    queue = FakeQueue()
    assert relay_once(restarted, queue).status == "SENT"
    assert json.loads(queue.sent[0]) == original
    if initial_sends:
        assert queue.sent[0] == base.sent[0]


@pytest.mark.parametrize("mode", ["duplicate", "conflict"])
def test_controlled_staged_delivery_does_not_mutate_persisted_result(session_factory, mode):
    command, result = _pair()
    _stage(session_factory, command, result)
    base = FakeQueue()
    controlled = ControlledQueue(
        base,
        command=command,
        run_id="ct15-staged-control",
        mode=mode,
        approved=True,
        expected_sender_id=EXPECTED_SENDER_ID,
    )
    assert relay_once(session_factory, controlled).status == "SENT"
    assert len(base.sent) == 2
    first, second = map(json.loads, base.sent)
    assert first["message_id"] == second["message_id"]
    assert (first["result_digest"] == second["result_digest"]) == (mode == "duplicate")
    with session_factory() as session:
        staged = session.scalar(select(PrivateStagedOutbox))
        assert staged.payload == first
        assert staged.delivered_at is not None
        assert (
            session.get(PrivateCommitStage, command.command_id).result_digest
            == (first["result_digest"])
        )


def test_retained_finalize_cannot_restore_purged_payload(session_factory):
    command, result = _pair()
    _stage(session_factory, command, result)
    operation_id = uuid4()
    prepare = _gate(command, result, "PREPARE", operation_id=operation_id, revision=1)
    assert (
        consume_once(
            session_factory, FakeQueue(deliveries=[_delivery(prepare)]), EXPECTED_SENDER_ID
        ).status
        == "APPLIED"
    )
    finalize = _gate(command, result, "FINALIZE", operation_id=operation_id, revision=2)
    base = FakeQueue(deliveries=[_delivery(finalize)])
    controlled = ControlledQueue(
        base,
        command=command,
        run_id="ct15-finalize-retention",
        mode="retain-finalize",
        approved=True,
        expected_sender_id=EXPECTED_SENDER_ID,
    )
    assert consume_once(session_factory, controlled, EXPECTED_SENDER_ID).status == "APPLIED"
    assert base.deleted == []
    with session_factory() as session:
        original_ack = session.get(PrivateCommitGateInbox, finalize.message_id).ack_message_id
    purge = _gate(command, result, "PURGE", operation_id=operation_id, revision=3)
    assert (
        consume_once(
            session_factory, FakeQueue(deliveries=[_delivery(purge)]), EXPECTED_SENDER_ID
        ).status
        == "APPLIED"
    )
    redelivery = FakeQueue(deliveries=[_delivery(finalize, "new-private-receipt")])
    assert consume_once(session_factory, redelivery, EXPECTED_SENDER_ID).status == "APPLIED"
    assert redelivery.deleted == ["new-private-receipt"]
    with session_factory() as session:
        row = session.get(PrivateCommitStage, command.command_id)
        assert row.state == "PURGED"
        assert row.result_payload is None
        assert session.scalar(select(PrivateStagedOutbox)).payload is None
        assert (
            session.get(PrivateCommitGateInbox, finalize.message_id).ack_message_id == original_ack
        )
        assert session.scalar(select(func.count()).select_from(PrivateCommitGateAck)) == 3
    # A previously unseen FINALIZE cannot perform a new transition after purge.
    late = _gate(command, result, "FINALIZE", operation_id=operation_id, revision=4)
    rejected = FakeQueue(deliveries=[_delivery(late)])
    assert consume_once(session_factory, rejected, EXPECTED_SENDER_ID).status == "REJECTED"
    assert rejected.deleted == []


def test_scoped_relay_does_not_get_stuck_behind_another_pending_owner(session_factory):
    first_command, first_result = _pair()
    target_command, target_result = _pair()
    _stage(session_factory, first_command, first_result, message_id=UUID(int=1))
    _stage(session_factory, target_command, target_result, message_id=UUID(int=2))
    base = FakeQueue()
    controlled = ControlledQueue(
        base,
        command=target_command,
        run_id="ct15-scoped-relay",
        mode="duplicate",
        approved=True,
        expected_sender_id=EXPECTED_SENDER_ID,
    )
    assert (
        relay_once(session_factory, controlled, command_id=target_command.command_id).status
        == "SENT"
    )
    assert len(base.sent) == 2
    assert json.loads(base.sent[0])["command"]["command_id"] == str(target_command.command_id)
    with session_factory() as session:
        assert (
            session.scalar(
                select(PrivateStagedOutbox).where(
                    PrivateStagedOutbox.command_id == first_command.command_id
                )
            ).delivered_at
            is None
        )
    incoming = FakeQueue(
        deliveries=[
            _delivery(
                _gate(target_command, target_result, "PREPARE", operation_id=uuid4(), revision=1)
            )
        ]
    )
    assert consume_once(session_factory, incoming, EXPECTED_SENDER_ID).status == "APPLIED"
    # Even a non-target staged row must not take priority over the target ACK.
    target_ack_queue = FakeQueue()
    assert (
        relay_once(session_factory, target_ack_queue, command_id=target_command.command_id).status
        == "SENT"
    )
    assert json.loads(target_ack_queue.sent[0])["message_type"] == (
        "w2.private.commit-gate-ack.proposal.v1"
    )


def test_scoped_relay_filters_competing_ack_candidates_deterministically(session_factory):
    commands = [_pair(), _pair()]
    for index, (command, result) in enumerate(commands, start=1):
        _stage(session_factory, command, result)
        assert (
            relay_once(session_factory, FakeQueue(), command_id=command.command_id).status == "SENT"
        )
        with session_factory.begin() as session:
            apply_commit_gate(
                session,
                _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1),
                ack_message_id=UUID(int=index),
                occurred_at=NOW,
            )
    target_command, _ = commands[1]
    queue = FakeQueue()
    assert relay_once(session_factory, queue, command_id=target_command.command_id).status == "SENT"
    assert json.loads(queue.sent[0])["message_id"] == str(UUID(int=2))
    with session_factory() as session:
        assert session.get(PrivateCommitGateAck, UUID(int=1)).delivered_at is None
