"""W1-adopted private PostgreSQL boundary, independent from queue transport.

The caller owns the transaction. Savepoints isolate storage conflicts; releasing
a savepoint is not a database commit and no relay/worker is registered here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Mapped, Session, mapped_column

from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateAckProposal,
    CommitGateCommand,
    StagedResultProposal,
    build_commit_gate_ack,
    build_staged_result,
    parse_commit_gate_command,
)
from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult
from epick_engine.source_collection.persistence import Base, bind_private_write_scope
from epick_engine.source_collection.private_scope import (
    PrivateGateAuthority,
    PrivateScopeRejected,
    PrivateTerminalCleanupAuthority,
    PrivateWriteScope,
    lock_private_gate_scope,
    lock_private_terminal_cleanup_scope,
    lock_private_write_scope,
)
from epick_engine.source_collection.w1_transport import (
    W1WireContractError,
    _parse_wire,
    _revalidate_model,
)


class PrivateCommitStage(Base):
    __tablename__ = "private_commit_stages"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_private_commit_stages_operation"),
        CheckConstraint(
            "state IN ('STAGED', 'PREPARED', 'FINALIZED', 'ABORTED', 'PURGED')",
            name="valid_state",
        ),
        CheckConstraint(
            "(state IN ('ABORTED', 'PURGED') AND result_payload IS NULL) OR "
            "(state IN ('STAGED', 'PREPARED', 'FINALIZED') AND ("
            "(payload_purged AND result_payload IS NULL) OR "
            "(NOT payload_purged AND result_payload IS NOT NULL)))",
            name="payload_matches_state",
        ),
        CheckConstraint(
            "stage_kind IN ('PRIVATE_ONLY', 'COLLECTION')",
            name="valid_stage_kind",
        ),
        CheckConstraint(
            "(private_scope_kind = 'PROJECT' AND project_id IS NOT NULL) OR "
            "(private_scope_kind = 'ACCOUNT' AND project_id IS NULL) OR "
            "private_scope_kind = 'UNKNOWN'",
            name="valid_private_scope",
        ),
        Index(
            "ix_private_commit_stages_owner_private_scope",
            "owner_ref",
            "private_scope_kind",
            "project_id",
        ),
    )

    command_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_ref: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    private_scope_kind: Mapped[str] = mapped_column(
        String(16),
        default="UNKNOWN",
        server_default=text("'UNKNOWN'"),
        nullable=False,
    )
    project_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    # Wire integers have no upper bound: canonical decimal text avoids bigint overflow.
    execution_fence: Mapped[str] = mapped_column(Text, nullable=False)
    owner_deletion_epoch: Mapped[str] = mapped_column(Text, nullable=False)
    result_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    operation_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    operation_revision: Mapped[str] = mapped_column(Text, nullable=False)
    max_purge_epoch: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    stage_kind: Mapped[str] = mapped_column(
        String(16),
        default="PRIVATE_ONLY",
        server_default=text("'PRIVATE_ONLY'"),
        nullable=False,
    )
    payload_purged: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )


class PrivateStagedOutbox(Base):
    __tablename__ = "private_staged_outbox"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_private_staged_outbox_command"),
        CheckConstraint(
            "(relay_claim_token IS NULL) = (relay_claim_expires_at IS NULL)",
            name="relay_claim_fields_together",
        ),
    )

    message_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    command_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("private_commit_stages.command_id"), nullable=False
    )
    wire_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    relay_claim_token: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    relay_claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PrivateCommitGateAck(Base):
    __tablename__ = "private_commit_gate_acks"
    __table_args__ = (
        CheckConstraint(
            "(relay_claim_token IS NULL) = (relay_claim_expires_at IS NULL)",
            name="relay_claim_fields_together",
        ),
        CheckConstraint(
            "wire_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="wire_digest_format",
        ),
    )

    message_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    command_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("private_commit_stages.command_id"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    wire_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    relay_claim_token: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    relay_claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PrivateCommitGateReceipt(Base):
    __tablename__ = "private_commit_gate_receipts"

    operation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    operation_revision: Mapped[str] = mapped_column(Text, primary_key=True)
    command_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("private_commit_stages.command_id"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ack_message_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("private_commit_gate_acks.message_id"),
        nullable=False,
    )


class PrivateCommitGateInbox(Base):
    __tablename__ = "private_commit_gate_inbox"

    message_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    command_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("private_commit_stages.command_id"), nullable=False
    )
    wire_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ack_message_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("private_commit_gate_acks.message_id"),
        nullable=False,
    )


class CommitGateRejected(ValueError):
    """Safe storage rejection without embedding private input in diagnostics."""


def _prepare_staged_outbox_for_terminal(
    session: Session,
    command_id: UUID,
) -> PrivateStagedOutbox | None:
    """Reject an active relay claim; reclaim an expired one using the DB clock."""

    staged = session.scalar(
        select(PrivateStagedOutbox)
        .where(PrivateStagedOutbox.command_id == command_id)
        .execution_options(populate_existing=True)
    )
    if staged is None:
        return None
    token = staged.relay_claim_token
    expires_at = staged.relay_claim_expires_at
    if token is None and expires_at is None:
        return staged
    if token is None or expires_at is None:
        raise CommitGateRejected("private staged relay claim is invalid")
    db_now = session.scalar(select(func.clock_timestamp()))
    if not isinstance(db_now, datetime) or db_now.tzinfo is None:
        raise CommitGateRejected("private staged relay clock unavailable")
    if expires_at > db_now:
        raise CommitGateRejected("private staged relay is active")
    staged.relay_claim_token = None
    staged.relay_claim_expires_at = None
    return staged


def cleanup_terminal_staged_outbox(
    session: Session,
    command_id: UUID,
    *,
    private_cleanup_authority: PrivateTerminalCleanupAuthority | None = None,
) -> None:
    """Tombstone one exact existing STAGED payload without creating gate state."""

    if not isinstance(command_id, UUID):
        raise CommitGateRejected("invalid private staged cleanup command")
    if not isinstance(private_cleanup_authority, PrivateTerminalCleanupAuthority):
        raise PrivateScopeRejected("a W1 terminal cleanup authority is required")
    if (
        private_cleanup_authority.cleanup_kind != "STAGED_OUTBOX"
        or private_cleanup_authority.command_id != command_id
    ):
        raise PrivateScopeRejected("private staged cleanup authority binding does not match")
    lock_private_terminal_cleanup_scope(session, private_cleanup_authority)
    with _storage_transaction(session, command_id):
        row = session.scalar(
            select(PrivateCommitStage)
            .where(PrivateCommitStage.command_id == command_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.state != "STAGED":
            raise CommitGateRejected("private staged cleanup target is unavailable")
        if (
            row.owner_ref != private_cleanup_authority.owner_user_id
            or row.job_id != private_cleanup_authority.job_id
            or row.execution_fence != str(private_cleanup_authority.execution_fence)
            or row.owner_deletion_epoch != str(private_cleanup_authority.owner_deletion_epoch)
            or row.private_scope_kind != private_cleanup_authority.kind
            or row.project_id != private_cleanup_authority.project_id
        ):
            raise PrivateScopeRejected("private staged cleanup scope does not match")
        staged = _prepare_staged_outbox_for_terminal(session, command_id)
        if staged is None:
            raise CommitGateRejected("private staged cleanup outbox is unavailable")
        row.payload_purged = True
        row.result_payload = None
        staged.payload = None
        _flush_private_storage(session)


def _hash(raw: dict[str, Any]) -> str:
    encoded = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_ack_wire(payload: dict[str, Any]) -> tuple[str, str]:
    """Return the exact canonical ACK body and its durable SHA-256 identity."""

    if not isinstance(payload, dict):
        raise ValueError("private commit-gate ACK payload must be an object")
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    digest = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    return body, digest


def lock_private_command(session: Session, command_id: UUID) -> None:
    if session.get_bind().dialect.name != "postgresql":
        raise CommitGateRejected("private commit-gate storage requires PostgreSQL")
    # Namespace + UUID, not owner, prevents alternate-owner claims bypassing the lock.
    # Lock before looking for a row; FOR UPDATE alone misses the absent-row race.
    key = int.from_bytes(
        hashlib.sha256(b"epick.w2.private.commit-gate.v1\0" + command_id.bytes).digest()[:8],
        "big",
        signed=True,
    )
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


_lock_command = lock_private_command


@contextmanager
def _storage_transaction(session: Session, command_id: UUID) -> Iterator[None]:
    try:
        with session.begin_nested():
            lock_private_command(session, command_id)
            yield
    except IntegrityError:
        # Unique delivery/operation collisions cannot leak driver parameters or
        # partially change the command, even if the caller catches and commits.
        raise CommitGateRejected("private commit-gate identity conflict") from None
    except StatementError:
        # JSONB data errors and DBAPI statement failures can include private SQL
        # parameters. Roll back the savepoint before exposing a fixed rejection.
        raise CommitGateRejected("private commit-gate data storage rejected") from None


def _flush_private_storage(session: Session) -> None:
    try:
        session.flush()
    except (TypeError, ValueError):
        # psycopg JSON serializers can raise these directly, without a DBAPI
        # wrapper. Catch only the flush boundary, not unrelated store logic.
        raise CommitGateRejected("private commit-gate data serialization rejected") from None


def _bound_row(
    row: PrivateCommitStage,
    *,
    owner: UUID,
    job: UUID,
    fence: str,
    epoch: int,
    digest: str,
    stage_kind: Literal["PRIVATE_ONLY", "COLLECTION"] | None = None,
    private_scope: PrivateWriteScope | PrivateGateAuthority | None = None,
) -> None:
    if (
        row.owner_ref != owner
        or row.job_id != job
        or row.execution_fence != fence
        or row.owner_deletion_epoch != str(epoch)
        or row.result_digest != digest
        or (stage_kind is not None and row.stage_kind != stage_kind)
    ):
        raise CommitGateRejected("private commit-gate binding mismatch")
    if private_scope is not None and (
        row.private_scope_kind != private_scope.kind or row.project_id != private_scope.project_id
    ):
        raise PrivateScopeRejected("private commit-gate scope binding does not match")


def _stored_ack(
    session: Session,
    message_id: UUID,
    *,
    for_update: bool = False,
) -> CommitGateAckProposal:
    row = (
        session.scalar(
            select(PrivateCommitGateAck)
            .where(PrivateCommitGateAck.message_id == message_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if for_update
        else session.get(PrivateCommitGateAck, message_id, populate_existing=True)
    )
    if row is None:
        raise CommitGateRejected("private commit-gate ACK unavailable")
    try:
        body, digest = canonical_ack_wire(row.payload)
        if not isinstance(row.wire_digest, str) or not hmac.compare_digest(row.wire_digest, digest):
            raise ValueError
        ack = CommitGateAckProposal.model_validate_json(body, strict=True)
        if ack.message_id != row.message_id or ack.command_id != row.command_id:
            raise ValueError
        return ack
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise CommitGateRejected("invalid persisted private commit-gate ACK") from None


def stage_private_result(
    session: Session,
    command: CollectionCommand,
    result: CollectionResult,
    *,
    message_id: UUID,
    occurred_at: datetime,
    stage_kind: Literal["PRIVATE_ONLY", "COLLECTION"] = "PRIVATE_ONLY",
    private_scope: PrivateWriteScope | None = None,
) -> StagedResultProposal:
    """Store an invisible result and submission together, without committing."""
    try:
        proposal = build_staged_result(
            command, result, message_id=message_id, occurred_at=occurred_at
        )
    except W1WireContractError:
        raise CommitGateRejected("invalid private staged-result context") from None
    raw = proposal.model_dump(mode="json")
    wire_hash = _hash(raw)
    command = proposal.command
    scope_kind, project_id = bind_private_write_scope(
        private_scope,
        owner_user_id=command.authenticated_owner_ref,
        owner_deletion_epoch=command.owner_deletion_epoch,
        command_id=command.command_id,
        job_id=command.job_id,
        project_ref=command.project_ref,
        bind_project_ref=True,
    )
    assert private_scope is not None
    lock_private_write_scope(session, private_scope)
    with _storage_transaction(session, command.command_id):
        row = session.get(PrivateCommitStage, command.command_id, populate_existing=True)
        delivery = session.get(PrivateStagedOutbox, message_id)
        if delivery is not None and (
            delivery.command_id != command.command_id or delivery.wire_hash != wire_hash
        ):
            raise CommitGateRejected("private staged-result message conflict")
        if row is not None:
            _bound_row(
                row,
                owner=command.authenticated_owner_ref,
                job=command.job_id,
                fence=command.execution_fence,
                epoch=command.owner_deletion_epoch,
                digest=proposal.result_digest,
                stage_kind=stage_kind,
                private_scope=private_scope,
            )
            if row.payload_purged:
                raise CommitGateRejected("private staged-result payload was purged")
            if row.state in {"ABORTED", "PURGED"}:
                raise CommitGateRejected("private staged-result command is terminal")
            existing = session.scalar(
                select(PrivateStagedOutbox).where(
                    PrivateStagedOutbox.command_id == command.command_id
                )
            )
            if existing is None or existing.payload is None:
                raise CommitGateRejected("private staged-result submission unavailable")
            try:
                return _parse_wire(
                    existing.payload, StagedResultProposal, label="persisted W2 stage"
                )
            except W1WireContractError:
                raise CommitGateRejected("invalid persisted private staged-result") from None
        session.add(
            PrivateCommitStage(
                command_id=command.command_id,
                owner_ref=command.authenticated_owner_ref,
                job_id=command.job_id,
                private_scope_kind=scope_kind,
                project_id=project_id,
                execution_fence=command.execution_fence,
                owner_deletion_epoch=str(command.owner_deletion_epoch),
                result_digest=proposal.result_digest,
                operation_id=None,
                operation_revision="0",
                max_purge_epoch=str(command.owner_deletion_epoch),
                state="STAGED",
                stage_kind=stage_kind,
                result_payload=proposal.result.model_dump(mode="json"),
            )
        )
        _flush_private_storage(session)
        session.add(
            PrivateStagedOutbox(
                message_id=message_id,
                command_id=command.command_id,
                wire_hash=wire_hash,
                payload=raw,
            )
        )
        _flush_private_storage(session)
    return proposal


def apply_commit_gate(
    session: Session,
    gate_command: CommitGateCommand,
    *,
    ack_message_id: UUID,
    occurred_at: datetime,
    missing_stage_kind: Literal["PRIVATE_ONLY", "COLLECTION"] = "PRIVATE_ONLY",
    private_gate_authority: PrivateGateAuthority | None = None,
) -> CommitGateAckProposal:
    """Apply binding/state/revision and persist the exact ACK in the same transaction."""
    try:
        gate = (
            _revalidate_model(gate_command, CommitGateCommand, label="private gate command")
            if isinstance(gate_command, CommitGateCommand)
            else parse_commit_gate_command(gate_command)
        )
        ack = build_commit_gate_ack(
            gate, outcome="APPLIED", message_id=ack_message_id, occurred_at=occurred_at
        )
    except W1WireContractError:
        raise CommitGateRejected("invalid private commit-gate context") from None
    raw = gate.model_dump(mode="json")
    wire_hash = _hash(raw)
    content_hash = _hash({k: v for k, v in raw.items() if k not in {"message_id", "issued_at"}})
    if not isinstance(private_gate_authority, PrivateGateAuthority):
        raise PrivateScopeRejected("a W1-issued private gate authority is required")
    private_gate_authority.assert_bound_to(gate, phase="APPLY")
    scope_kind = private_gate_authority.kind
    project_id = private_gate_authority.project_id
    lock_private_gate_scope(session, private_gate_authority)
    with _storage_transaction(session, gate.command_id):
        row = session.get(PrivateCommitStage, gate.command_id, populate_existing=True)
        if row is not None:
            _bound_row(
                row,
                owner=gate.authenticated_owner_ref,
                job=gate.job_id,
                fence=str(gate.execution_fence),
                epoch=gate.owner_deletion_epoch,
                digest=gate.result_digest,
                private_scope=private_gate_authority,
            )
        inbox = session.get(PrivateCommitGateInbox, gate.message_id)
        if inbox is not None:
            if inbox.wire_hash != wire_hash or inbox.command_id != gate.command_id:
                raise CommitGateRejected("private commit-gate message conflict")
            if row is None:
                raise CommitGateRejected("private commit-gate staged result unavailable")
            return _stored_ack(session, inbox.ack_message_id)
        if row is not None:
            if row.operation_id is not None and row.operation_id != gate.operation_id:
                raise CommitGateRejected("private commit-gate operation conflict")
        receipt = session.get(
            PrivateCommitGateReceipt, (gate.operation_id, str(gate.operation_revision))
        )
        if receipt is not None:
            if receipt.command_id != gate.command_id or receipt.content_hash != content_hash:
                raise CommitGateRejected("private commit-gate revision conflict")
            stored = _stored_ack(session, receipt.ack_message_id)
            session.add(
                PrivateCommitGateInbox(
                    message_id=gate.message_id,
                    command_id=gate.command_id,
                    wire_hash=wire_hash,
                    ack_message_id=receipt.ack_message_id,
                )
            )
            _flush_private_storage(session)
            return stored
        if row is None:
            if gate.action not in {"ABORT", "PURGE"}:
                raise CommitGateRejected("private commit-gate staged result unavailable")
            row = PrivateCommitStage(
                command_id=gate.command_id,
                owner_ref=gate.authenticated_owner_ref,
                job_id=gate.job_id,
                private_scope_kind=scope_kind,
                project_id=project_id,
                execution_fence=str(gate.execution_fence),
                owner_deletion_epoch=str(gate.owner_deletion_epoch),
                result_digest=gate.result_digest,
                operation_id=gate.operation_id,
                operation_revision="0",
                max_purge_epoch=str(gate.owner_deletion_epoch),
                state="ABORTED",
                stage_kind=missing_stage_kind,
                result_payload=None,
            )
            session.add(row)
        elif gate.operation_revision <= int(row.operation_revision):
            raise CommitGateRejected("private commit-gate stale revision")
        staged: PrivateStagedOutbox | None = None
        if gate.action in {"ABORT", "PURGE"}:
            staged = _prepare_staged_outbox_for_terminal(session, gate.command_id)
        if gate.action == "PREPARE":
            if row.payload_purged:
                raise CommitGateRejected("private commit-gate payload was purged")
            if row.state != "STAGED":
                raise CommitGateRejected("private commit-gate PREPARE requires STAGED")
            row.state = "PREPARED"
        elif gate.action == "FINALIZE":
            if row.payload_purged:
                raise CommitGateRejected("private commit-gate payload was purged")
            if row.state != "PREPARED":
                raise CommitGateRejected("private commit-gate FINALIZE requires PREPARED")
            row.state = "FINALIZED"
        elif gate.action == "ABORT":
            if row.state in {"FINALIZED", "PURGED"} or (
                row.state == "ABORTED" and int(row.operation_revision) != 0
            ):
                raise CommitGateRejected("private commit-gate ABORT requires nonterminal result")
            row.state = "ABORTED"
            row.result_payload = None
        else:
            epoch = gate.purge_owner_deletion_epoch
            if epoch is None or epoch <= int(row.max_purge_epoch):
                raise CommitGateRejected("private commit-gate PURGE requires newer deletion epoch")
            row.max_purge_epoch = str(epoch)
            row.state = "PURGED"
            row.result_payload = None
        row.operation_id = gate.operation_id
        row.operation_revision = str(gate.operation_revision)
        if gate.action in {"ABORT", "PURGE"}:
            if staged is not None:
                staged.payload = None
        _flush_private_storage(session)
        ack_payload = ack.model_dump(mode="json")
        _, ack_wire_digest = canonical_ack_wire(ack_payload)
        session.add(
            PrivateCommitGateAck(
                message_id=ack.message_id,
                command_id=gate.command_id,
                payload=ack_payload,
                wire_digest=ack_wire_digest,
            )
        )
        _flush_private_storage(session)
        session.add(
            PrivateCommitGateReceipt(
                operation_id=gate.operation_id,
                operation_revision=str(gate.operation_revision),
                command_id=gate.command_id,
                content_hash=content_hash,
                ack_message_id=ack.message_id,
            )
        )
        session.add(
            PrivateCommitGateInbox(
                message_id=gate.message_id,
                command_id=gate.command_id,
                wire_hash=wire_hash,
                ack_message_id=ack.message_id,
            )
        )
        _flush_private_storage(session)
    return ack


def read_finalized_result(
    session: Session, *, owner_ref: UUID, command_id: UUID
) -> CollectionResult | None:
    """Read only finalized results for the caller-authenticated owner reference."""
    if not isinstance(owner_ref, UUID) or not isinstance(command_id, UUID):
        raise CommitGateRejected("invalid private finalized-result identity")
    row = session.scalar(
        select(PrivateCommitStage)
        .where(
            PrivateCommitStage.command_id == command_id,
            PrivateCommitStage.owner_ref == owner_ref,
            PrivateCommitStage.state == "FINALIZED",
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    if row.payload_purged:
        return None
    try:
        return _parse_wire(
            row.result_payload, CollectionResult, label="persisted W2 private result"
        )
    except W1WireContractError:
        raise CommitGateRejected("invalid persisted private finalized result") from None
