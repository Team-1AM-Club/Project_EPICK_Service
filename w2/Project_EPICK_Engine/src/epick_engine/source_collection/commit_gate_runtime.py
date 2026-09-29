"""Queue-independent runtime for the W2 private commit gate."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateAckProposal,
    CommitGateCommand,
    parse_commit_gate_command,
)
from epick_engine.source_collection.commit_gate_store import (
    PrivateCommitGateAck,
    PrivateCommitStage,
    PrivateStagedOutbox,
    _lock_command,
    apply_commit_gate,
    canonical_ack_wire,
    cleanup_terminal_staged_outbox,
)
from epick_engine.source_collection.persistence import PrivateDeletionOwnerState
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateGateAuthority,
    PrivateScopeRejected,
    PrivateTerminalCleanupAuthority,
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
    lock_private_write_scope,
)
from epick_engine.source_collection.w1_lookup_client import W1LookupClientError
from epick_engine.source_collection.w1_private_authority_contracts import (
    CleanupKind,
    GateAuthorityResponse,
    GatePhase,
    GateScopeLookupResponse,
    PrivateWriteAuthorityResponse,
    TerminalCleanupAuthorityResponse,
    W1GateBinding,
    W1PrivateBinding,
)
from epick_engine.source_collection.w1_transport import W1WireContractError

# W1's adopted command outbox bound is 16 KiB. Outbound payloads retain the
# earlier SQS-safe 256 KiB ceiling because staged results can exceed commands.
MAX_MESSAGE_BYTES = 16_384
MAX_OUTBOUND_MESSAGE_BYTES = 262_144
DEFAULT_RELAY_CLAIM_LEASE_SECONDS = 300

type ConsumeStatus = Literal[
    "EMPTY",
    "APPLIED",
    "DELETE_FAILED",
    "MALFORMED",
    "UNAUTHENTICATED",
    "REJECTED",
    "RECEIVE_FAILED",
]
type RelayStatus = Literal["EMPTY", "SENT", "SEND_FAILED"]
type BeforeSend = Callable[[str, Mapping[str, object]], None]


@dataclass(frozen=True, slots=True)
class QueueDelivery:
    receipt_handle: str
    body: str
    sender_id: str | None


class Queue(Protocol):
    def receive(self) -> Sequence[QueueDelivery]: ...

    def delete(self, receipt_handle: str) -> None: ...

    def send(self, body: str) -> None: ...


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...

    def begin(self) -> AbstractContextManager[Session]: ...


class GateApplier(Protocol):
    def __call__(
        self,
        session: Session,
        gate_command: CommitGateCommand,
        *,
        ack_message_id: UUID,
        occurred_at: datetime,
        private_gate_authority: PrivateGateAuthority | None = None,
    ) -> CommitGateAckProposal: ...


class PrivateWriteAuthorityProvider(Protocol):
    """Authenticate one commit-gate authority decision outside its wire body."""

    def __call__(self, gate: CommitGateCommand) -> PrivateWriteAuthorityDecision: ...


class GateAuthorityClient(Protocol):
    def lookup_gate_scope(
        self,
        gate: W1GateBinding,
        phase: GatePhase,
    ) -> GateScopeLookupResponse: ...

    def authorize_gate(
        self,
        gate: W1GateBinding,
        phase: GatePhase,
        scope: PrivateDeletionScope,
    ) -> GateAuthorityResponse: ...


class RelayAuthorityClient(GateAuthorityClient, Protocol):
    def authorize_write(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
    ) -> PrivateWriteAuthorityResponse: ...

    def authorize_terminal_cleanup(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
        cleanup_kind: CleanupKind,
    ) -> TerminalCleanupAuthorityResponse: ...


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    status: ConsumeStatus


@dataclass(frozen=True, slots=True)
class RelayResult:
    status: RelayStatus


class _CurrentWriteSemanticallyDenied(RuntimeError):
    """A W1 HTTP 403 that permits only an exact terminal-cleanup attempt."""


@dataclass(frozen=True, slots=True)
class _StagedRelaySubject:
    binding: W1PrivateBinding
    scope: PrivateDeletionScope
    body: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class _AckRelaySubject:
    ack: CommitGateAckProposal
    body: str
    wire_digest: str
    payload: dict[str, object]


type _RelaySubject = _StagedRelaySubject | _AckRelaySubject


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _strict_json_object(body: str, *, max_bytes: int = MAX_MESSAGE_BYTES) -> dict[str, object]:
    if not isinstance(body, str) or len(body.encode("utf-8")) > max_bytes:
        raise ValueError("invalid queue body")
    value = json.loads(
        body,
        object_pairs_hook=_object_without_duplicate_keys,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("queue body must be a JSON object")
    return value


def _sender_matches(sender_id: str | None, expected_sender_id: str) -> bool:
    if not isinstance(sender_id, str) or not isinstance(expected_sender_id, str):
        return False
    if not expected_sender_id or ":" in expected_sender_id:
        return False
    stable_id = sender_id.partition(":")[0]
    try:
        stable_bytes = stable_id.encode("ascii")
        expected_bytes = expected_sender_id.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(stable_bytes, expected_bytes)


def _wire_body(payload: dict[str, object]) -> str:
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if len(body.encode("utf-8")) > MAX_OUTBOUND_MESSAGE_BYTES:
        raise ValueError("persisted queue body is too large")
    return body


def _get_relay_outbox(
    session: Session,
    kind: Literal["STAGED", "ACK"],
    message_id: UUID,
) -> PrivateStagedOutbox | PrivateCommitGateAck | None:
    if kind == "STAGED":
        return session.get(PrivateStagedOutbox, message_id, populate_existing=True)
    return session.get(PrivateCommitGateAck, message_id, populate_existing=True)


def _stored_relay_binding(
    session: Session,
    command_id: UUID,
) -> tuple[W1PrivateBinding, PrivateDeletionScope] | None:
    stage = session.get(PrivateCommitStage, command_id, populate_existing=True)
    if stage is None or stage.private_scope_kind not in {"ACCOUNT", "PROJECT"}:
        return None
    try:
        owner_deletion_epoch = int(stage.owner_deletion_epoch)
        execution_fence = int(stage.execution_fence)
        if (
            str(owner_deletion_epoch) != stage.owner_deletion_epoch
            or str(execution_fence) != stage.execution_fence
        ):
            raise ValueError
        kind: Literal["ACCOUNT", "PROJECT"] = (
            "ACCOUNT" if stage.private_scope_kind == "ACCOUNT" else "PROJECT"
        )
        scope = PrivateDeletionScope(
            kind=kind,
            project_id=stage.project_id,
        )
        return (
            W1PrivateBinding(
                owner_user_id=stage.owner_ref,
                owner_deletion_epoch=owner_deletion_epoch,
                command_id=stage.command_id,
                job_id=stage.job_id,
                execution_fence=execution_fence,
            ),
            scope,
        )
    except (TypeError, ValueError):
        return None


def _private_response_matches(
    response: PrivateWriteAuthorityResponse | TerminalCleanupAuthorityResponse,
    binding: W1PrivateBinding,
) -> bool:
    return (
        response.owner_user_id == binding.owner_user_id
        and response.owner_deletion_epoch == binding.owner_deletion_epoch
        and response.command_id == binding.command_id
        and response.job_id == binding.job_id
        and response.execution_fence == binding.execution_fence
    )


def _authorize_staged_relay(
    subject: _StagedRelaySubject,
    authority_client: RelayAuthorityClient,
) -> PrivateWriteScope:
    try:
        response = authority_client.authorize_write(subject.binding, subject.scope)
    except W1LookupClientError as exc:
        if exc.code == "HTTP_403":
            raise _CurrentWriteSemanticallyDenied from exc
        raise RuntimeError("W1 private relay authority is invalid") from exc
    except Exception as exc:
        raise RuntimeError("W1 private relay authority is invalid") from exc
    if (
        not isinstance(response, PrivateWriteAuthorityResponse)
        or not _private_response_matches(response, subject.binding)
        or response.scope != subject.scope.to_mapping()
    ):
        raise RuntimeError("W1 private relay authority is invalid")
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=subject.binding.owner_user_id,
            owner_deletion_epoch=subject.binding.owner_deletion_epoch,
            scope=subject.scope,
            authority_ref=response.authority_ref,
            command_id=subject.binding.command_id,
            job_id=subject.binding.job_id,
        )
    )


def _lock_staged_relay_scope(
    session: Session,
    command_id: UUID,
    subject: _StagedRelaySubject,
    decision: PrivateWriteScope,
    *,
    lock_command: bool,
) -> None:
    lock_private_write_scope(session, decision)
    if lock_command:
        _lock_command(session, command_id)
    current = _stored_relay_binding(session, command_id)
    if current != (subject.binding, subject.scope):
        raise RuntimeError("persisted private relay scope unavailable")


def _lock_ack_owner(session: Session, decision: PrivateGateAuthority) -> None:
    owner = session.scalar(
        select(PrivateDeletionOwnerState)
        .where(PrivateDeletionOwnerState.owner_user_id == decision.owner_user_id)
        .with_for_update()
    )
    if owner is None:
        raise PrivateScopeRejected("private ACK relay owner state was not found")


def _gate_response_matches(
    response: GateScopeLookupResponse | GateAuthorityResponse,
    gate: W1GateBinding,
    *,
    phase: GatePhase,
) -> bool:
    return (
        response.owner_user_id == gate.private.owner_user_id
        and response.owner_deletion_epoch == gate.private.owner_deletion_epoch
        and response.command_id == gate.private.command_id
        and response.job_id == gate.private.job_id
        and response.execution_fence == gate.private.execution_fence
        and response.operation_id == gate.operation_id
        and response.operation_revision == gate.operation_revision
        and response.action == gate.action
        and response.phase == phase
        and response.result_digest == gate.result_digest
        and response.purge_owner_deletion_epoch == gate.purge_owner_deletion_epoch
    )


def _persisted_gate_scope(session: Session, gate: CommitGateCommand) -> PrivateDeletionScope | None:
    stage = session.get(PrivateCommitStage, gate.command_id, populate_existing=True)
    if stage is None:
        return None
    if (
        stage.owner_ref != gate.authenticated_owner_ref
        or stage.job_id != gate.job_id
        or stage.execution_fence != str(gate.execution_fence)
        or stage.owner_deletion_epoch != str(gate.owner_deletion_epoch)
        or stage.result_digest != gate.result_digest
    ):
        raise RuntimeError("persisted private gate scope binding does not match")
    if stage.private_scope_kind == "ACCOUNT" and stage.project_id is None:
        return PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    if stage.private_scope_kind == "PROJECT" and isinstance(stage.project_id, UUID):
        return PrivateDeletionScope(kind="PROJECT", project_id=stage.project_id)
    raise RuntimeError("persisted private gate scope is unclassified")


def _persisted_ack_scope(
    session: Session,
    ack: CommitGateAckProposal,
) -> PrivateDeletionScope | None:
    stage = session.get(PrivateCommitStage, ack.command_id, populate_existing=True)
    if stage is None:
        return None
    if (
        stage.owner_ref != ack.authenticated_owner_ref
        or stage.job_id != ack.job_id
        or stage.execution_fence != str(ack.execution_fence)
        or stage.owner_deletion_epoch != str(ack.owner_deletion_epoch)
        or stage.result_digest != ack.result_digest
    ):
        raise RuntimeError("persisted private ACK binding does not match")
    if stage.private_scope_kind == "UNKNOWN":
        return None
    if stage.private_scope_kind == "ACCOUNT" and stage.project_id is None:
        return PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    if stage.private_scope_kind == "PROJECT" and isinstance(stage.project_id, UUID):
        return PrivateDeletionScope(kind="PROJECT", project_id=stage.project_id)
    raise RuntimeError("persisted private ACK scope is unclassified")


def _resolve_ack_relay_authority(
    session_factory: SessionFactory,
    ack: CommitGateAckProposal,
    authority_client: GateAuthorityClient,
) -> PrivateGateAuthority:
    binding = W1GateBinding.from_ack(ack)
    with session_factory() as session:
        scope = _persisted_ack_scope(session, ack)
    if scope is None:
        lookup = authority_client.lookup_gate_scope(binding, "ACK_RELAY")
        if not isinstance(lookup, GateScopeLookupResponse) or not _gate_response_matches(
            lookup,
            binding,
            phase="ACK_RELAY",
        ):
            raise RuntimeError("W1 ACK relay scope lookup is invalid")
        try:
            scope = PrivateDeletionScope.from_mapping(lookup.scope)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError("W1 ACK relay scope lookup is invalid") from exc
    response = authority_client.authorize_gate(binding, "ACK_RELAY", scope)
    if (
        not isinstance(response, GateAuthorityResponse)
        or not _gate_response_matches(response, binding, phase="ACK_RELAY")
        or response.scope != scope.to_mapping()
    ):
        raise RuntimeError("W1 ACK relay authority is invalid")
    decision = PrivateGateAuthority.from_w1_response(response)
    if decision.scope != scope:
        raise RuntimeError("W1 ACK relay authority scope is invalid")
    return decision


def _parse_ack_subject(
    outbox: PrivateCommitGateAck,
    *,
    message_id: UUID,
    command_id: UUID,
    expected_body: str | None = None,
    expected_wire_digest: str | None = None,
) -> _AckRelaySubject:
    try:
        body, wire_digest = canonical_ack_wire(outbox.payload)
        if not isinstance(outbox.wire_digest, str) or not hmac.compare_digest(
            outbox.wire_digest, wire_digest
        ):
            raise ValueError("persisted private ACK digest does not match")
        if expected_body is not None and body != expected_body:
            raise ValueError("persisted private ACK body changed")
        if expected_wire_digest is not None and not hmac.compare_digest(
            wire_digest, expected_wire_digest
        ):
            raise ValueError("persisted private ACK digest changed")
        ack = CommitGateAckProposal.model_validate_json(body, strict=True)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RuntimeError("persisted private ACK body is invalid") from exc
    if ack.message_id != message_id or ack.command_id != command_id:
        raise RuntimeError("persisted private ACK identity does not match")
    return _AckRelaySubject(
        ack=ack,
        body=body,
        wire_digest=wire_digest,
        payload=_strict_json_object(body, max_bytes=MAX_OUTBOUND_MESSAGE_BYTES),
    )


def _outbox_matches_subject(
    outbox: PrivateStagedOutbox | PrivateCommitGateAck,
    subject: _RelaySubject,
) -> bool:
    if outbox.payload is None:
        return False
    if isinstance(subject, _AckRelaySubject):
        if not isinstance(outbox, PrivateCommitGateAck):
            return False
        try:
            body, wire_digest = canonical_ack_wire(outbox.payload)
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return False
        return (
            isinstance(outbox.wire_digest, str)
            and hmac.compare_digest(outbox.wire_digest, wire_digest)
            and hmac.compare_digest(wire_digest, subject.wire_digest)
            and body == subject.body
        )
    return isinstance(outbox, PrivateStagedOutbox) and _wire_body(outbox.payload) == subject.body


def _load_relay_subject(
    session_factory: SessionFactory,
    kind: Literal["STAGED", "ACK"],
    message_id: UUID,
    command_id: UUID,
    *,
    expected_body: str | None = None,
    expected_wire_digest: str | None = None,
) -> _RelaySubject:
    with session_factory() as session:
        outbox = _get_relay_outbox(session, kind, message_id)
        if (
            outbox is None
            or outbox.command_id != command_id
            or outbox.delivered_at is not None
            or outbox.payload is None
        ):
            raise RuntimeError("persisted private relay outbox is unavailable")
        if kind == "ACK":
            if not isinstance(outbox, PrivateCommitGateAck):
                raise RuntimeError("persisted private ACK outbox is invalid")
            subject: _RelaySubject = _parse_ack_subject(
                outbox,
                message_id=message_id,
                command_id=command_id,
                expected_body=expected_body,
                expected_wire_digest=expected_wire_digest,
            )
        else:
            stored = _stored_relay_binding(session, command_id)
            if stored is None:
                raise RuntimeError("persisted private relay binding is unavailable")
            binding, scope = stored
            body = _wire_body(outbox.payload)
            subject = _StagedRelaySubject(
                binding=binding,
                scope=scope,
                body=body,
                payload=_strict_json_object(body, max_bytes=MAX_OUTBOUND_MESSAGE_BYTES),
            )
    if expected_body is not None and subject.body != expected_body:
        raise RuntimeError("persisted private relay body changed")
    if expected_wire_digest is not None and not isinstance(subject, _AckRelaySubject):
        raise RuntimeError("persisted private relay digest changed")
    return subject


def _authorize_relay_subject(
    session_factory: SessionFactory,
    subject: _RelaySubject,
    authority_client: RelayAuthorityClient,
) -> PrivateWriteScope | PrivateGateAuthority:
    if isinstance(subject, _StagedRelaySubject):
        return _authorize_staged_relay(subject, authority_client)
    return _resolve_ack_relay_authority(session_factory, subject.ack, authority_client)


def _lock_relay_subject(
    session: Session,
    command_id: UUID,
    subject: _RelaySubject,
    decision: PrivateWriteScope | PrivateGateAuthority,
    *,
    lock_command: bool,
) -> None:
    if isinstance(subject, _StagedRelaySubject):
        if not isinstance(decision, PrivateWriteScope):
            raise PrivateScopeRejected("a current-write relay decision is required")
        _lock_staged_relay_scope(
            session,
            command_id,
            subject,
            decision,
            lock_command=lock_command,
        )
        return
    if not isinstance(decision, PrivateGateAuthority) or decision.phase != "ACK_RELAY":
        raise PrivateScopeRejected("an ACK_RELAY gate decision is required")
    binding = W1GateBinding.from_ack(subject.ack)
    if (
        decision.owner_user_id != binding.private.owner_user_id
        or decision.owner_deletion_epoch != binding.private.owner_deletion_epoch
        or decision.command_id != binding.private.command_id
        or decision.job_id != binding.private.job_id
        or decision.execution_fence != binding.private.execution_fence
        or decision.operation_id != binding.operation_id
        or decision.operation_revision != binding.operation_revision
        or decision.action != binding.action
        or decision.result_digest != binding.result_digest
        or decision.purge_owner_deletion_epoch != binding.purge_owner_deletion_epoch
    ):
        raise PrivateScopeRejected("private ACK_RELAY authority binding does not match")
    _lock_ack_owner(session, decision)
    if lock_command:
        _lock_command(session, command_id)
    local_scope = _persisted_ack_scope(session, subject.ack)
    if local_scope is not None and local_scope != decision.scope:
        raise PrivateScopeRejected("private ACK_RELAY scope does not match")


def _cleanup_denied_staged(
    session_factory: SessionFactory,
    subject: _StagedRelaySubject,
    authority_client: RelayAuthorityClient,
) -> None:
    response = authority_client.authorize_terminal_cleanup(
        subject.binding,
        subject.scope,
        "STAGED_OUTBOX",
    )
    if (
        not isinstance(response, TerminalCleanupAuthorityResponse)
        or not _private_response_matches(response, subject.binding)
        or response.scope != subject.scope.to_mapping()
        or response.cleanup_kind != "STAGED_OUTBOX"
        or response.allowed_effect != "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY"
    ):
        raise RuntimeError("W1 terminal STAGED cleanup authority is invalid")
    decision = PrivateTerminalCleanupAuthority.from_w1_response(response)
    decision.assert_bound_to(subject.binding, cleanup_kind="STAGED_OUTBOX")
    with session_factory.begin() as session:
        cleanup_terminal_staged_outbox(
            session,
            subject.binding.command_id,
            private_cleanup_authority=decision,
        )


def _resolve_gate_apply_authority(
    session_factory: SessionFactory,
    gate: CommitGateCommand,
    authority_client: GateAuthorityClient,
) -> PrivateGateAuthority:
    binding = W1GateBinding.from_gate(gate)
    with session_factory() as session:
        scope = _persisted_gate_scope(session, gate)
    if scope is None:
        lookup = authority_client.lookup_gate_scope(binding, "APPLY")
        if not isinstance(lookup, GateScopeLookupResponse) or not _gate_response_matches(
            lookup,
            binding,
            phase="APPLY",
        ):
            raise RuntimeError("W1 gate scope lookup is invalid")
        try:
            scope = PrivateDeletionScope.from_mapping(lookup.scope)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError("W1 gate scope lookup is invalid") from exc
    response = authority_client.authorize_gate(binding, "APPLY", scope)
    if (
        not isinstance(response, GateAuthorityResponse)
        or not _gate_response_matches(response, binding, phase="APPLY")
        or response.scope != scope.to_mapping()
    ):
        raise RuntimeError("W1 gate APPLY authority is invalid")
    decision = PrivateGateAuthority.from_w1_response(response)
    decision.assert_bound_to(gate, phase="APPLY")
    if decision.scope != scope:
        raise RuntimeError("W1 gate APPLY authority scope is invalid")
    return decision


def consume_once(
    session_factory: SessionFactory,
    queue: Queue,
    expected_sender_id: str,
    *,
    clock: Callable[[], datetime] = _utc_now,
    message_id_factory: Callable[[], UUID] = uuid4,
    apply_gate: GateApplier | None = None,
    authority_provider: PrivateWriteAuthorityProvider | None = None,
    private_authority_client: GateAuthorityClient | None = None,
) -> ConsumeResult:
    """Consume at most one gate command; rejected receipts remain for retry/DLQ."""

    try:
        deliveries = queue.receive()
    except Exception:
        return ConsumeResult(status="RECEIVE_FAILED")
    if not deliveries:
        return ConsumeResult(status="EMPTY")
    delivery = deliveries[0]
    if not _sender_matches(delivery.sender_id, expected_sender_id):
        return ConsumeResult(status="UNAUTHENTICATED")
    try:
        payload = _strict_json_object(delivery.body)
        gate = parse_commit_gate_command(payload)
    except (TypeError, ValueError, UnicodeError, RecursionError, W1WireContractError):
        return ConsumeResult(status="MALFORMED")

    gate_applier = apply_commit_gate if apply_gate is None else apply_gate
    try:
        if private_authority_client is None:
            raise RuntimeError("protected W1 private authority client is required")
        private_gate_authority = _resolve_gate_apply_authority(
            session_factory,
            gate,
            private_authority_client,
        )
        with session_factory.begin() as session:
            ack = gate_applier(
                session,
                gate,
                ack_message_id=message_id_factory(),
                occurred_at=clock(),
                private_gate_authority=private_gate_authority,
            )
            persisted_ack = session.get(
                PrivateCommitGateAck, ack.message_id, populate_existing=True
            )
            if persisted_ack is None:
                raise RuntimeError("persisted private commit-gate ACK unavailable")
            # An accepted W1 replay asks for the same durable ACK again. Preserve
            # its stored identity/outcome and only re-arm its delivery marker.
            persisted_ack.delivered_at = None
    except Exception:
        return ConsumeResult(status="REJECTED")

    try:
        queue.delete(delivery.receipt_handle)
    except Exception:
        return ConsumeResult(status="DELETE_FAILED")
    return ConsumeResult(status="APPLIED")


def relay_once(
    session_factory: SessionFactory,
    queue: Queue,
    *,
    clock: Callable[[], datetime] = _utc_now,
    command_id: UUID | None = None,
    authority_client: RelayAuthorityClient | None = None,
    before_send: BeforeSend | None = None,
    claim_token_factory: Callable[[], object] = uuid4,
    claim_lease_seconds: int = DEFAULT_RELAY_CLAIM_LEASE_SECONDS,
) -> RelayResult:
    """Relay one persisted wire, optionally scoped to a synthetic command."""

    claimed: tuple[Literal["STAGED", "ACK"], UUID, UUID, UUID, str, str | None] | None = None

    def release_claim() -> None:
        if claimed is None or authority_client is None:
            return
        (
            kind,
            message_id,
            claimed_command_id,
            claim_token,
            claimed_body,
            claimed_wire_digest,
        ) = claimed
        try:
            subject = _load_relay_subject(
                session_factory,
                kind,
                message_id,
                claimed_command_id,
                expected_body=claimed_body,
                expected_wire_digest=claimed_wire_digest,
            )
            try:
                decision = _authorize_relay_subject(
                    session_factory,
                    subject,
                    authority_client,
                )
            except _CurrentWriteSemanticallyDenied:
                if isinstance(subject, _StagedRelaySubject):
                    _cleanup_denied_staged(session_factory, subject, authority_client)
                return
            with session_factory.begin() as session:
                _lock_relay_subject(
                    session,
                    claimed_command_id,
                    subject,
                    decision,
                    lock_command=True,
                )
                outbox = _get_relay_outbox(session, kind, message_id)
                if (
                    outbox is not None
                    and outbox.delivered_at is None
                    and outbox.relay_claim_token == claim_token
                    and _outbox_matches_subject(outbox, subject)
                ):
                    outbox.relay_claim_token = None
                    outbox.relay_claim_expires_at = None
        except Exception:
            # A failed cleanup remains recoverable by DB-clock lease expiry.
            return

    try:
        if (
            type(command_id) not in {UUID, type(None)}
            or not isinstance(claim_lease_seconds, int)
            or isinstance(claim_lease_seconds, bool)
            or not 1 <= claim_lease_seconds <= 43_200
        ):
            return RelayResult(status="SEND_FAILED")
        with session_factory() as session:
            staged = session.scalar(
                select(PrivateStagedOutbox)
                .where(
                    PrivateStagedOutbox.delivered_at.is_(None),
                    PrivateStagedOutbox.payload.is_not(None),
                    or_(
                        PrivateStagedOutbox.relay_claim_token.is_(None),
                        PrivateStagedOutbox.relay_claim_expires_at <= func.clock_timestamp(),
                    ),
                    *(
                        (PrivateStagedOutbox.command_id == command_id,)
                        if command_id is not None
                        else ()
                    ),
                )
                .order_by(PrivateStagedOutbox.message_id)
                .limit(1)
            )
            if staged is not None:
                candidate: tuple[Literal["STAGED", "ACK"], UUID, UUID] | None = (
                    "STAGED",
                    staged.message_id,
                    staged.command_id,
                )
            else:
                ack = session.scalar(
                    select(PrivateCommitGateAck)
                    .where(
                        PrivateCommitGateAck.delivered_at.is_(None),
                        or_(
                            PrivateCommitGateAck.relay_claim_token.is_(None),
                            PrivateCommitGateAck.relay_claim_expires_at <= func.clock_timestamp(),
                        ),
                        *(
                            (PrivateCommitGateAck.command_id == command_id,)
                            if command_id is not None
                            else ()
                        ),
                    )
                    .order_by(PrivateCommitGateAck.message_id)
                    .limit(1)
                )
                candidate = None if ack is None else ("ACK", ack.message_id, ack.command_id)
        if candidate is None:
            return RelayResult(status="EMPTY")
        if authority_client is None:
            return RelayResult(status="SEND_FAILED")

        kind, message_id, candidate_command_id = candidate
        claim_token = claim_token_factory()
        if not isinstance(claim_token, UUID):
            return RelayResult(status="SEND_FAILED")
        subject = _load_relay_subject(
            session_factory,
            kind,
            message_id,
            candidate_command_id,
        )
        try:
            claim_decision = _authorize_relay_subject(
                session_factory,
                subject,
                authority_client,
            )
        except _CurrentWriteSemanticallyDenied:
            if isinstance(subject, _StagedRelaySubject):
                try:
                    _cleanup_denied_staged(session_factory, subject, authority_client)
                except Exception:
                    pass
            return RelayResult(status="SEND_FAILED")
        with session_factory.begin() as session:
            _lock_relay_subject(
                session,
                candidate_command_id,
                subject,
                claim_decision,
                lock_command=True,
            )
            outbox = _get_relay_outbox(session, kind, message_id)
            db_now = session.scalar(select(func.clock_timestamp()))
            if not isinstance(db_now, datetime) or db_now.tzinfo is None:
                raise RuntimeError("database clock unavailable")
            if (
                outbox is None
                or outbox.command_id != candidate_command_id
                or outbox.delivered_at is not None
                or (kind == "STAGED" and outbox.payload is None)
                or (
                    outbox.relay_claim_token is not None
                    and (
                        outbox.relay_claim_expires_at is None
                        or outbox.relay_claim_expires_at > db_now
                    )
                )
            ):
                return RelayResult(status="EMPTY")
            if not _outbox_matches_subject(outbox, subject):
                raise RuntimeError("persisted queue payload changed during relay claim")
            outbox.relay_claim_token = claim_token
            outbox.relay_claim_expires_at = db_now + timedelta(seconds=claim_lease_seconds)
        claimed = (
            kind,
            message_id,
            candidate_command_id,
            claim_token,
            subject.body,
            subject.wire_digest if isinstance(subject, _AckRelaySubject) else None,
        )

        send_subject = _load_relay_subject(
            session_factory,
            kind,
            message_id,
            candidate_command_id,
            expected_body=subject.body,
            expected_wire_digest=(
                subject.wire_digest if isinstance(subject, _AckRelaySubject) else None
            ),
        )
        send_decision = _authorize_relay_subject(
            session_factory,
            send_subject,
            authority_client,
        )
        with session_factory.begin() as session:
            _lock_relay_subject(
                session,
                candidate_command_id,
                send_subject,
                send_decision,
                lock_command=False,
            )
            outbox = _get_relay_outbox(session, kind, message_id)
            if (
                outbox is None
                or outbox.command_id != candidate_command_id
                or outbox.delivered_at is not None
                or outbox.relay_claim_token != claim_token
                or outbox.relay_claim_expires_at is None
            ):
                raise RuntimeError("persisted queue payload changed during relay authorization")
            if not _outbox_matches_subject(outbox, send_subject):
                raise RuntimeError("persisted queue payload changed during relay authorization")
            if before_send is not None:
                before_send(kind, send_subject.payload)
            queue.send(send_subject.body)
            _lock_command(session, candidate_command_id)
            if isinstance(send_subject, _StagedRelaySubject):
                if _stored_relay_binding(session, candidate_command_id) != (
                    send_subject.binding,
                    send_subject.scope,
                ):
                    raise RuntimeError("persisted private relay scope changed")
            else:
                if not isinstance(send_decision, PrivateGateAuthority):
                    raise RuntimeError("private ACK relay authority changed")
                local_scope = _persisted_ack_scope(session, send_subject.ack)
                if local_scope is not None and local_scope != send_decision.scope:
                    raise RuntimeError("persisted private ACK scope changed")
            outbox = _get_relay_outbox(session, kind, message_id)
            if (
                outbox is None
                or outbox.command_id != candidate_command_id
                or outbox.delivered_at is not None
                or outbox.relay_claim_token != claim_token
                or outbox.relay_claim_expires_at is None
                or not _outbox_matches_subject(outbox, send_subject)
            ):
                raise RuntimeError("persisted queue payload changed during relay send")
            outbox.delivered_at = clock()
            outbox.relay_claim_token = None
            outbox.relay_claim_expires_at = None
        claimed = None
        return RelayResult(status="SENT")
    except Exception:
        release_claim()
        return RelayResult(status="SEND_FAILED")
