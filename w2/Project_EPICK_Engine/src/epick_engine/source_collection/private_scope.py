"""Private-write authority values, scope validation, and owner-first locking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Self
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from epick_engine.source_collection.persistence import (
    PrivateDeletionOwnerState,
    PrivateDeletionProjectTombstone,
)
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope

if TYPE_CHECKING:
    from epick_engine.source_collection.commit_gate_contracts import CommitGateCommand
    from epick_engine.source_collection.w1_private_authority_contracts import (
        GateAuthorityResponse,
        TerminalCleanupAuthorityResponse,
        W1PrivateBinding,
    )

SIGNED_64_MAX = 9_223_372_036_854_775_807


class PrivateScopeRejected(RuntimeError):
    """Raised when a private-write authority value cannot pass its fence."""


class ScopeUnclassified(PrivateScopeRejected):
    """Raised when a private row has no authenticated account or Project scope."""


@dataclass(frozen=True, slots=True)
class PrivateWriteAuthorityDecision:
    """Shape-validated value supplied by a trusted authentication adapter.

    Constructing this value does not authenticate W1. Task 3 owns the production
    adapter/provider that may create it after authenticating the authority decision.
    """

    owner_user_id: UUID
    owner_deletion_epoch: int
    scope: PrivateDeletionScope
    authority_ref: str
    command_id: UUID | None = None
    job_id: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.owner_user_id, UUID):
            raise PrivateScopeRejected("owner binding must be a UUID")
        if (
            isinstance(self.owner_deletion_epoch, bool)
            or not isinstance(self.owner_deletion_epoch, int)
            or not 0 <= self.owner_deletion_epoch <= SIGNED_64_MAX
        ):
            raise PrivateScopeRejected("owner deletion epoch is outside signed 64-bit range")
        if not isinstance(self.scope, PrivateDeletionScope):
            raise PrivateScopeRejected("authority decision requires a validated v2 scope")
        if not isinstance(self.authority_ref, str) or not self.authority_ref.strip():
            raise PrivateScopeRejected("authority reference must be nonempty")
        if self.command_id is not None and not isinstance(self.command_id, UUID):
            raise PrivateScopeRejected("command binding must be a UUID")
        if self.job_id is not None and not isinstance(self.job_id, UUID):
            raise PrivateScopeRejected("job binding must be a UUID")


@dataclass(frozen=True, slots=True)
class PrivateWriteScope:
    """Write-fence proof built from one upstream authority decision value."""

    decision: PrivateWriteAuthorityDecision

    def __post_init__(self) -> None:
        if not isinstance(self.decision, PrivateWriteAuthorityDecision):
            raise PrivateScopeRejected("a private write authority decision is required")

    @property
    def owner_user_id(self) -> UUID:
        return self.decision.owner_user_id

    @property
    def owner_deletion_epoch(self) -> int:
        return self.decision.owner_deletion_epoch

    @property
    def kind(self) -> Literal["ACCOUNT", "PROJECT"]:
        return self.decision.scope.kind

    @property
    def project_id(self) -> UUID | None:
        return self.decision.scope.project_id

    @property
    def authority_ref(self) -> str:
        return self.decision.authority_ref

    @property
    def command_id(self) -> UUID | None:
        return self.decision.command_id

    @property
    def job_id(self) -> UUID | None:
        return self.decision.job_id

    def assert_bound_to(
        self,
        *,
        owner_user_id: UUID,
        owner_deletion_epoch: int,
        command_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> None:
        """Reject a proof reused for a different owner, epoch, command, or job."""

        if not isinstance(owner_user_id, UUID):
            raise PrivateScopeRejected("target owner binding must be a UUID")
        if (
            isinstance(owner_deletion_epoch, bool)
            or not isinstance(owner_deletion_epoch, int)
            or not 0 <= owner_deletion_epoch <= SIGNED_64_MAX
        ):
            raise PrivateScopeRejected("target owner deletion epoch is outside signed 64-bit range")
        if command_id is not None and not isinstance(command_id, UUID):
            raise PrivateScopeRejected("target command binding must be a UUID")
        if job_id is not None and not isinstance(job_id, UUID):
            raise PrivateScopeRejected("target job binding must be a UUID")
        if self.owner_user_id != owner_user_id:
            raise PrivateScopeRejected("private scope owner binding does not match")
        if self.owner_deletion_epoch != owner_deletion_epoch:
            raise PrivateScopeRejected("private scope epoch binding does not match")
        if self.command_id != command_id:
            raise PrivateScopeRejected("private scope command binding does not match")
        if self.job_id != job_id:
            raise PrivateScopeRejected("private scope job binding does not match")


@dataclass(frozen=True, slots=True)
class PrivateTerminalCleanupAuthority:
    """One exact W1 terminal-cleanup decision with no write semantics."""

    owner_user_id: UUID
    owner_deletion_epoch: int
    scope: PrivateDeletionScope
    authority_ref: str
    command_id: UUID
    job_id: UUID
    execution_fence: int
    cleanup_kind: Literal["RESERVATION_RELEASE", "CLAIM_RELEASE", "STAGED_OUTBOX"]
    allowed_effect: Literal["OWNER_LOCKED_PRIVATE_CLEANUP_ONLY"]

    def __post_init__(self) -> None:
        if not isinstance(self.owner_user_id, UUID):
            raise PrivateScopeRejected("private cleanup authority owner must be a UUID")
        if (
            isinstance(self.owner_deletion_epoch, bool)
            or not isinstance(self.owner_deletion_epoch, int)
            or not 0 <= self.owner_deletion_epoch <= SIGNED_64_MAX
        ):
            raise PrivateScopeRejected("private cleanup authority owner epoch is invalid")
        if not isinstance(self.scope, PrivateDeletionScope):
            raise PrivateScopeRejected("private cleanup authority scope is invalid")
        if not isinstance(self.authority_ref, str) or not self.authority_ref.strip():
            raise PrivateScopeRejected("private cleanup authority reference is invalid")
        for uuid_value, label in (
            (self.command_id, "command"),
            (self.job_id, "Job"),
        ):
            if not isinstance(uuid_value, UUID):
                raise PrivateScopeRejected(f"private cleanup authority {label} must be a UUID")
        if (
            isinstance(self.execution_fence, bool)
            or not isinstance(self.execution_fence, int)
            or not 1 <= self.execution_fence <= SIGNED_64_MAX
        ):
            raise PrivateScopeRejected("private cleanup authority execution fence is invalid")
        if self.cleanup_kind not in {"RESERVATION_RELEASE", "CLAIM_RELEASE", "STAGED_OUTBOX"}:
            raise PrivateScopeRejected("private cleanup authority kind is invalid")
        if self.allowed_effect != "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY":
            raise PrivateScopeRejected("private cleanup authority effect is invalid")

    @classmethod
    def from_w1_response(cls, response: TerminalCleanupAuthorityResponse) -> Self:
        from epick_engine.source_collection.w1_private_authority_contracts import (  # noqa: PLC0415
            TerminalCleanupAuthorityResponse,
        )

        if not isinstance(response, TerminalCleanupAuthorityResponse):
            raise PrivateScopeRejected("a W1 terminal cleanup authority response is required")
        try:
            scope = PrivateDeletionScope.from_mapping(response.scope)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise PrivateScopeRejected("private cleanup authority scope is invalid") from exc
        return cls(
            owner_user_id=response.owner_user_id,
            owner_deletion_epoch=response.owner_deletion_epoch,
            scope=scope,
            authority_ref=response.authority_ref,
            command_id=response.command_id,
            job_id=response.job_id,
            execution_fence=response.execution_fence,
            cleanup_kind=response.cleanup_kind,
            allowed_effect=response.allowed_effect,
        )

    @property
    def kind(self) -> Literal["ACCOUNT", "PROJECT"]:
        return self.scope.kind

    @property
    def project_id(self) -> UUID | None:
        return self.scope.project_id

    def assert_bound_to(
        self,
        binding: W1PrivateBinding,
        *,
        cleanup_kind: Literal["RESERVATION_RELEASE", "CLAIM_RELEASE", "STAGED_OUTBOX"],
    ) -> None:
        from epick_engine.source_collection.w1_private_authority_contracts import (  # noqa: PLC0415
            W1PrivateBinding,
        )

        if not isinstance(binding, W1PrivateBinding) or (
            self.owner_user_id != binding.owner_user_id
            or self.owner_deletion_epoch != binding.owner_deletion_epoch
            or self.command_id != binding.command_id
            or self.job_id != binding.job_id
            or self.execution_fence != binding.execution_fence
            or self.cleanup_kind != cleanup_kind
        ):
            raise PrivateScopeRejected("private cleanup authority binding does not match")


@dataclass(frozen=True, slots=True)
class PrivateGateAuthority:
    """One exact W1-issued gate decision, distinct from current-write proof."""

    owner_user_id: UUID
    owner_deletion_epoch: int
    scope: PrivateDeletionScope
    authority_ref: str
    command_id: UUID
    job_id: UUID
    execution_fence: int
    operation_id: UUID
    operation_revision: int
    action: Literal["PREPARE", "FINALIZE", "ABORT", "PURGE"]
    phase: Literal["APPLY", "ACK_RELAY"]
    result_digest: str
    purge_owner_deletion_epoch: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.owner_user_id, UUID):
            raise PrivateScopeRejected("private gate authority owner must be a UUID")
        if (
            isinstance(self.owner_deletion_epoch, bool)
            or not isinstance(self.owner_deletion_epoch, int)
            or not 0 <= self.owner_deletion_epoch <= SIGNED_64_MAX
        ):
            raise PrivateScopeRejected("private gate authority owner epoch is invalid")
        if not isinstance(self.scope, PrivateDeletionScope):
            raise PrivateScopeRejected("private gate authority scope is invalid")
        if not isinstance(self.authority_ref, str) or not self.authority_ref.strip():
            raise PrivateScopeRejected("private gate authority reference is invalid")
        for uuid_value, label in (
            (self.command_id, "command"),
            (self.job_id, "Job"),
            (self.operation_id, "operation"),
        ):
            if not isinstance(uuid_value, UUID):
                raise PrivateScopeRejected(f"private gate authority {label} must be a UUID")
        for integer_value, label in (
            (self.execution_fence, "execution fence"),
            (self.operation_revision, "operation revision"),
        ):
            if (
                isinstance(integer_value, bool)
                or not isinstance(integer_value, int)
                or not 1 <= integer_value <= SIGNED_64_MAX
            ):
                raise PrivateScopeRejected(f"private gate authority {label} is invalid")
        if self.action not in {"PREPARE", "FINALIZE", "ABORT", "PURGE"}:
            raise PrivateScopeRejected("private gate authority action is invalid")
        if self.phase not in {"APPLY", "ACK_RELAY"}:
            raise PrivateScopeRejected("private gate authority phase is invalid")
        if (
            not isinstance(self.result_digest, str)
            or len(self.result_digest) != 71
            or not self.result_digest.startswith("sha256:")
        ):
            raise PrivateScopeRejected("private gate authority digest is invalid")
        try:
            int(self.result_digest[7:], 16)
        except ValueError:
            raise PrivateScopeRejected("private gate authority digest is invalid") from None
        if self.action == "PURGE":
            if (
                isinstance(self.purge_owner_deletion_epoch, bool)
                or not isinstance(self.purge_owner_deletion_epoch, int)
                or not 1 <= self.purge_owner_deletion_epoch <= SIGNED_64_MAX
                or self.purge_owner_deletion_epoch <= self.owner_deletion_epoch
            ):
                raise PrivateScopeRejected("private gate authority purge epoch is invalid")
        elif self.purge_owner_deletion_epoch is not None:
            raise PrivateScopeRejected("private gate authority purge epoch is invalid")

    @classmethod
    def from_w1_response(cls, response: GateAuthorityResponse) -> Self:
        from epick_engine.source_collection.w1_private_authority_contracts import (  # noqa: PLC0415
            GateAuthorityResponse,
        )

        if not isinstance(response, GateAuthorityResponse):
            raise PrivateScopeRejected("a W1 gate authority response is required")
        try:
            scope = PrivateDeletionScope.from_mapping(response.scope)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise PrivateScopeRejected("private gate authority scope is invalid") from exc
        return cls(
            owner_user_id=response.owner_user_id,
            owner_deletion_epoch=response.owner_deletion_epoch,
            scope=scope,
            authority_ref=response.authority_ref,
            command_id=response.command_id,
            job_id=response.job_id,
            execution_fence=response.execution_fence,
            operation_id=response.operation_id,
            operation_revision=response.operation_revision,
            action=response.action,
            phase=response.phase,
            result_digest=response.result_digest,
            purge_owner_deletion_epoch=response.purge_owner_deletion_epoch,
        )

    @property
    def kind(self) -> Literal["ACCOUNT", "PROJECT"]:
        return self.scope.kind

    @property
    def project_id(self) -> UUID | None:
        return self.scope.project_id

    def assert_bound_to(
        self,
        gate: CommitGateCommand,
        *,
        phase: Literal["APPLY", "ACK_RELAY"],
    ) -> None:
        from epick_engine.source_collection.commit_gate_contracts import (  # noqa: PLC0415
            CommitGateCommand,
        )

        if not isinstance(gate, CommitGateCommand) or (
            self.owner_user_id != gate.authenticated_owner_ref
            or self.owner_deletion_epoch != gate.owner_deletion_epoch
            or self.command_id != gate.command_id
            or self.job_id != gate.job_id
            or self.execution_fence != gate.execution_fence
            or self.operation_id != gate.operation_id
            or self.operation_revision != gate.operation_revision
            or self.action != gate.action
            or self.phase != phase
            or self.result_digest != gate.result_digest
            or self.purge_owner_deletion_epoch != gate.purge_owner_deletion_epoch
        ):
            raise PrivateScopeRejected("private gate authority binding does not match")


def _lock_private_owner_state(
    session: Session,
    owner_user_id: UUID,
) -> PrivateDeletionOwnerState:
    session.execute(
        postgresql_insert(PrivateDeletionOwnerState)
        .values(
            owner_user_id=owner_user_id,
            latest_epoch=0,
            account_deleted=False,
        )
        .on_conflict_do_nothing(index_elements=["owner_user_id"])
    )
    owner_state = session.scalar(
        select(PrivateDeletionOwnerState)
        .where(PrivateDeletionOwnerState.owner_user_id == owner_user_id)
        .with_for_update()
    )
    if owner_state is None:
        raise RuntimeError("private deletion owner state row was not found")
    return owner_state


def _lock_existing_private_owner_state(
    session: Session,
    owner_user_id: UUID,
) -> PrivateDeletionOwnerState:
    owner_state = session.scalar(
        select(PrivateDeletionOwnerState)
        .where(PrivateDeletionOwnerState.owner_user_id == owner_user_id)
        .with_for_update()
    )
    if owner_state is None:
        raise PrivateScopeRejected("private cleanup owner state was not found")
    return owner_state


def _assert_current_private_scope(
    session: Session,
    *,
    owner_state: PrivateDeletionOwnerState,
    owner_user_id: UUID,
    owner_deletion_epoch: int,
    kind: Literal["ACCOUNT", "PROJECT"],
    project_id: UUID | None,
) -> None:
    if owner_state.account_deleted:
        raise PrivateScopeRejected("private write rejected by account tombstone")
    if owner_deletion_epoch != owner_state.latest_epoch:
        raise PrivateScopeRejected("private write proof does not bind the current epoch")
    if kind == "PROJECT":
        tombstone = session.scalar(
            select(PrivateDeletionProjectTombstone)
            .where(
                PrivateDeletionProjectTombstone.owner_user_id == owner_user_id,
                PrivateDeletionProjectTombstone.project_id == project_id,
            )
            .with_for_update()
        )
        if tombstone is not None:
            raise PrivateScopeRejected("private write rejected by project tombstone")


def lock_private_gate_scope(session: Session, decision: PrivateGateAuthority) -> None:
    """Lock owner first and enforce current or historical gate policy."""

    if not isinstance(decision, PrivateGateAuthority):
        raise PrivateScopeRejected("a W1-issued private gate authority is required")
    if decision.phase != "APPLY":
        raise PrivateScopeRejected("private gate authority phase does not permit APPLY")
    owner_state = _lock_private_owner_state(session, decision.owner_user_id)
    if decision.action in {"PREPARE", "FINALIZE"}:
        _assert_current_private_scope(
            session,
            owner_state=owner_state,
            owner_user_id=decision.owner_user_id,
            owner_deletion_epoch=decision.owner_deletion_epoch,
            kind=decision.kind,
            project_id=decision.project_id,
        )
    elif decision.action == "PURGE":
        purge_epoch = decision.purge_owner_deletion_epoch
        assert purge_epoch is not None
        if purge_epoch < owner_state.latest_epoch:
            raise PrivateScopeRejected("private gate PURGE would regress owner deletion epoch")


def lock_private_terminal_cleanup_scope(
    session: Session,
    decision: PrivateTerminalCleanupAuthority,
) -> None:
    """Lock an existing owner without requiring its old epoch to remain current."""

    if not isinstance(decision, PrivateTerminalCleanupAuthority):
        raise PrivateScopeRejected("a W1 terminal cleanup authority is required")
    if decision.allowed_effect != "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY":
        raise PrivateScopeRejected("private cleanup authority effect is invalid")
    _lock_existing_private_owner_state(session, decision.owner_user_id)


def lock_private_write_scope(session: Session, proof: PrivateWriteScope) -> None:
    """Lock the owner fence first and reject deleted or non-current write scope."""

    if not isinstance(proof, PrivateWriteScope):
        raise PrivateScopeRejected("a private write scope from an authority decision is required")

    owner_state = _lock_private_owner_state(session, proof.owner_user_id)
    _assert_current_private_scope(
        session,
        owner_state=owner_state,
        owner_user_id=proof.owner_user_id,
        owner_deletion_epoch=proof.owner_deletion_epoch,
        kind=proof.kind,
        project_id=proof.project_id,
    )
