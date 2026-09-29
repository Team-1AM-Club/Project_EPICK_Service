"""Durable reservation and short-lived claim storage for W2 collection runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from epick_engine.source_collection.commit_gate_store import (
    PrivateCommitStage,
    lock_private_command,
)
from epick_engine.source_collection.persistence import (
    CollectionRuntimeAttempt,
    Source,
    SourcePolicyDecision,
    bind_private_write_scope,
)
from epick_engine.source_collection.private_scope import (
    PrivateScopeRejected,
    PrivateTerminalCleanupAuthority,
    PrivateWriteScope,
    lock_private_terminal_cleanup_scope,
    lock_private_write_scope,
)
from epick_engine.source_collection.w1_private_authority_contracts import (
    CleanupKind,
    W1PrivateBinding,
)
from epick_engine.source_collection.w1_transport import (
    W1CommandDispatch,
    W1DirectSourceRegistrationDispatch,
    W1Dispatch,
    W1WireContractError,
    _revalidate_model,
)


class CollectionRuntimeConflict(RuntimeError):
    """The same private command identity was reused with different immutable input."""


class CollectionRuntimeBusy(RuntimeError):
    """Another unexpired DB-clock claim owns this reserved command."""


type SessionFactory = Callable[[], Session]
type UUIDFactory = Callable[[], UUID]


def dispatch_digest(dispatch: W1Dispatch) -> str:
    payload = dispatch.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_dispatch(dispatch: W1Dispatch) -> W1Dispatch:
    try:
        if isinstance(dispatch, W1CommandDispatch):
            return _revalidate_model(dispatch, W1CommandDispatch, label="W1 command dispatch")
        if isinstance(dispatch, W1DirectSourceRegistrationDispatch):
            return _revalidate_model(
                dispatch,
                W1DirectSourceRegistrationDispatch,
                label="W1 direct registration dispatch",
            )
    except W1WireContractError:
        pass
    raise CollectionRuntimeConflict("invalid collection runtime dispatch") from None


def _assert_bound_attempt(
    attempt: CollectionRuntimeAttempt,
    dispatch: W1Dispatch,
    digest: str,
    *,
    private_scope: PrivateWriteScope | None = None,
) -> None:
    command = dispatch.payload
    if (
        attempt.dispatch_digest != digest
        or attempt.owner_ref != command.authenticated_owner_ref
        or attempt.job_id != command.job_id
        or attempt.source_id != command.source_id
        or attempt.company_id != command.company_id
    ):
        raise CollectionRuntimeConflict("collection runtime dispatch binding conflict")
    if private_scope is not None and (
        attempt.private_scope_kind != private_scope.kind
        or attempt.project_id != private_scope.project_id
    ):
        raise PrivateScopeRejected("collection runtime private scope does not match")


def load_bound_collection_attempt(
    session: Session,
    dispatch: W1Dispatch,
) -> CollectionRuntimeAttempt | None:
    """Load an immutable dispatch-bound attempt without consulting current policy."""

    validated = _validated_dispatch(dispatch)
    digest = dispatch_digest(validated)
    attempt = session.get(
        CollectionRuntimeAttempt,
        validated.payload.command_id,
        populate_existing=True,
    )
    if attempt is not None:
        _assert_bound_attempt(attempt, validated, digest)
    return attempt


def reserve_collection_attempt(
    session: Session,
    dispatch: W1Dispatch,
    effective_policy_revision: int,
    now: datetime,
    uuid_factory: UUIDFactory,
    private_scope: PrivateWriteScope | None = None,
) -> CollectionRuntimeAttempt:
    """Reserve one Source-local observation order in the caller-owned transaction."""

    validated = _validated_dispatch(dispatch)
    if (
        not isinstance(effective_policy_revision, int)
        or isinstance(effective_policy_revision, bool)
        or effective_policy_revision <= 0
    ):
        raise CollectionRuntimeConflict("effective policy revision must be positive")
    command = validated.payload
    digest = dispatch_digest(validated)
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
    lock_private_command(session, command.command_id)
    terminal_stage = session.scalar(
        select(PrivateCommitStage)
        .where(
            PrivateCommitStage.command_id == command.command_id,
            PrivateCommitStage.state.in_(("ABORTED", "PURGED")),
        )
        .execution_options(populate_existing=True)
    )
    if terminal_stage is not None:
        raise CollectionRuntimeConflict("collection runtime command is terminal")

    attempt = session.scalar(
        select(CollectionRuntimeAttempt)
        .where(CollectionRuntimeAttempt.command_id == command.command_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is not None:
        _assert_bound_attempt(
            attempt,
            validated,
            digest,
            private_scope=private_scope,
        )
        if attempt.effective_policy_revision != effective_policy_revision:
            raise CollectionRuntimeConflict("collection runtime policy binding conflict")
        if attempt.state != "RESERVED":
            raise CollectionRuntimeConflict("collection runtime attempt is terminal")

    source = session.scalar(
        select(Source)
        .where(Source.source_id == command.source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None or source.company_id != command.company_id:
        raise CollectionRuntimeConflict("collection runtime source binding conflict")

    current_policy_revision = session.scalar(
        select(func.max(SourcePolicyDecision.revision)).where(
            SourcePolicyDecision.source_id == command.source_id
        )
    )
    if current_policy_revision != effective_policy_revision:
        raise CollectionRuntimeConflict("collection runtime policy revision is stale")

    if attempt is not None:
        return attempt

    source.pointer_update_mode = "FINALIZE_GATE"
    source.next_observation_order += 1
    attempt = CollectionRuntimeAttempt(
        command_id=command.command_id,
        attempt_id=uuid_factory(),
        dispatch_digest=digest,
        owner_ref=command.authenticated_owner_ref,
        job_id=command.job_id,
        private_scope_kind=scope_kind,
        project_id=project_id,
        source_id=command.source_id,
        company_id=command.company_id,
        observation_order=source.next_observation_order,
        effective_policy_revision=effective_policy_revision,
        state="RESERVED",
        claim_token=None,
        claim_expires_at=None,
        observation_id=None,
        source_version_id=None,
        created_at=now,
        updated_at=now,
    )
    session.add(attempt)
    session.flush()
    return attempt


def _validate_claim_identity(command_id: UUID, claim_token: UUID) -> None:
    if not isinstance(command_id, UUID) or not isinstance(claim_token, UUID):
        raise CollectionRuntimeConflict("invalid collection runtime claim identity")


def _validate_lease_seconds(lease_seconds: int) -> None:
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds <= 0:
        raise CollectionRuntimeConflict("collection runtime lease must be positive")


def _validate_dispatch_digest_value(expected_dispatch_digest: str) -> None:
    if (
        not isinstance(expected_dispatch_digest, str)
        or len(expected_dispatch_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_dispatch_digest)
    ):
        raise CollectionRuntimeConflict("expected collection runtime dispatch digest is invalid")


def _lock_reserved_attempt(
    session: Session,
    command_id: UUID,
    private_scope: PrivateWriteScope | None,
    expected_dispatch_digest: str,
) -> CollectionRuntimeAttempt:
    if private_scope is None or private_scope.job_id is None:
        raise PrivateScopeRejected("a command- and job-bound private write scope is required")
    bind_private_write_scope(
        private_scope,
        owner_user_id=private_scope.owner_user_id,
        owner_deletion_epoch=private_scope.owner_deletion_epoch,
        command_id=command_id,
        job_id=private_scope.job_id,
    )
    lock_private_write_scope(session, private_scope)
    lock_private_command(session, command_id)
    attempt = session.scalar(
        select(CollectionRuntimeAttempt)
        .where(CollectionRuntimeAttempt.command_id == command_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None or attempt.state != "RESERVED":
        raise CollectionRuntimeConflict("collection runtime attempt is not reserved")
    if attempt.dispatch_digest != expected_dispatch_digest:
        raise CollectionRuntimeConflict("collection runtime dispatch binding conflict")
    if (
        attempt.owner_ref != private_scope.owner_user_id
        or attempt.job_id != private_scope.job_id
        or attempt.private_scope_kind != private_scope.kind
        or attempt.project_id != private_scope.project_id
    ):
        raise PrivateScopeRejected("collection runtime private scope does not match")
    return attempt


def _lock_releasable_attempt(
    session: Session,
    command_id: UUID,
    private_scope: PrivateWriteScope | PrivateTerminalCleanupAuthority | None,
    private_binding: W1PrivateBinding | None,
    expected_dispatch_digest: str,
) -> CollectionRuntimeAttempt:
    if isinstance(private_scope, PrivateWriteScope):
        if private_binding is not None:
            raise PrivateScopeRejected("current write release cannot carry cleanup binding")
        return _lock_reserved_attempt(
            session,
            command_id,
            private_scope,
            expected_dispatch_digest,
        )
    if not isinstance(private_scope, PrivateTerminalCleanupAuthority):
        raise PrivateScopeRejected("a private release authority is required")
    return _lock_terminal_cleanup_attempt(
        session,
        command_id,
        private_scope,
        private_binding,
        expected_dispatch_digest,
        cleanup_kind="CLAIM_RELEASE",
    )


def _lock_terminal_cleanup_attempt(
    session: Session,
    command_id: UUID,
    private_scope: PrivateTerminalCleanupAuthority,
    private_binding: W1PrivateBinding | None,
    expected_dispatch_digest: str,
    *,
    cleanup_kind: CleanupKind,
) -> CollectionRuntimeAttempt:
    if not isinstance(private_binding, W1PrivateBinding):
        raise PrivateScopeRejected("terminal cleanup requires the original private binding")
    private_scope.assert_bound_to(private_binding, cleanup_kind=cleanup_kind)
    if private_scope.command_id != command_id:
        raise PrivateScopeRejected("private cleanup command binding does not match")
    lock_private_terminal_cleanup_scope(session, private_scope)
    lock_private_command(session, command_id)
    attempt = session.scalar(
        select(CollectionRuntimeAttempt)
        .where(CollectionRuntimeAttempt.command_id == command_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None or attempt.state != "RESERVED":
        raise CollectionRuntimeConflict("collection runtime attempt is not reserved")
    if attempt.dispatch_digest != expected_dispatch_digest:
        raise CollectionRuntimeConflict("collection runtime dispatch binding conflict")
    if (
        attempt.owner_ref != private_scope.owner_user_id
        or attempt.job_id != private_scope.job_id
        or attempt.private_scope_kind != private_scope.kind
        or attempt.project_id != private_scope.project_id
    ):
        raise PrivateScopeRejected("collection runtime cleanup scope does not match")
    return attempt


def _database_now(session: Session) -> datetime:
    value = session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise CollectionRuntimeConflict("collection runtime database clock unavailable")
    return value


def _detach_after_flush(
    session: Session,
    attempt: CollectionRuntimeAttempt,
) -> CollectionRuntimeAttempt:
    session.flush()
    session.expunge(attempt)
    return attempt


def claim_collection_attempt(
    session_factory: SessionFactory,
    command_id: UUID,
    *,
    claim_token: UUID,
    lease_seconds: int,
    expected_dispatch_digest: str,
    private_scope: PrivateWriteScope | None = None,
) -> CollectionRuntimeAttempt:
    """Claim a RESERVED attempt in one self-contained DB-clock transaction."""

    _validate_claim_identity(command_id, claim_token)
    _validate_lease_seconds(lease_seconds)
    _validate_dispatch_digest_value(expected_dispatch_digest)
    with session_factory() as session, session.begin():
        attempt = _lock_reserved_attempt(
            session,
            command_id,
            private_scope,
            expected_dispatch_digest,
        )
        database_now = _database_now(session)
        if (
            attempt.claim_token is not None
            and attempt.claim_token != claim_token
            and attempt.claim_expires_at is not None
            and attempt.claim_expires_at > database_now
        ):
            raise CollectionRuntimeBusy("collection runtime attempt is already claimed")
        attempt.claim_token = claim_token
        attempt.claim_expires_at = database_now + timedelta(seconds=lease_seconds)
        attempt.updated_at = database_now
        return _detach_after_flush(session, attempt)


def renew_collection_claim(
    session_factory: SessionFactory,
    command_id: UUID,
    *,
    claim_token: UUID,
    lease_seconds: int,
    expected_dispatch_digest: str,
    private_scope: PrivateWriteScope | None = None,
) -> CollectionRuntimeAttempt:
    """Renew only the caller's active RESERVED claim using the database clock."""

    _validate_claim_identity(command_id, claim_token)
    _validate_lease_seconds(lease_seconds)
    _validate_dispatch_digest_value(expected_dispatch_digest)
    with session_factory() as session, session.begin():
        attempt = _lock_reserved_attempt(
            session,
            command_id,
            private_scope,
            expected_dispatch_digest,
        )
        database_now = _database_now(session)
        if (
            attempt.claim_token != claim_token
            or attempt.claim_expires_at is None
            or attempt.claim_expires_at <= database_now
        ):
            raise CollectionRuntimeConflict("collection runtime claim token is not active")
        attempt.claim_expires_at = database_now + timedelta(seconds=lease_seconds)
        attempt.updated_at = database_now
        return _detach_after_flush(session, attempt)


def release_collection_claim(
    session_factory: SessionFactory,
    command_id: UUID,
    *,
    claim_token: UUID,
    expected_dispatch_digest: str,
    private_scope: PrivateWriteScope | PrivateTerminalCleanupAuthority | None = None,
    private_binding: W1PrivateBinding | None = None,
) -> CollectionRuntimeAttempt:
    """Release only the caller's active RESERVED claim in one root transaction."""

    _validate_claim_identity(command_id, claim_token)
    _validate_dispatch_digest_value(expected_dispatch_digest)
    with session_factory() as session, session.begin():
        attempt = _lock_releasable_attempt(
            session,
            command_id,
            private_scope,
            private_binding,
            expected_dispatch_digest,
        )
        database_now = _database_now(session)
        if (
            attempt.claim_token != claim_token
            or attempt.claim_expires_at is None
            or attempt.claim_expires_at <= database_now
        ):
            raise CollectionRuntimeConflict("collection runtime claim token is not active")
        attempt.claim_token = None
        attempt.claim_expires_at = None
        attempt.updated_at = database_now
        return _detach_after_flush(session, attempt)


def release_collection_reservation(
    session_factory: SessionFactory,
    command_id: UUID,
    *,
    expected_dispatch_digest: str,
    private_scope: PrivateTerminalCleanupAuthority | None = None,
    private_binding: W1PrivateBinding | None = None,
) -> CollectionRuntimeAttempt:
    """Tombstone only an exact unclaimed RESERVED row under terminal authority."""

    if not isinstance(command_id, UUID):
        raise CollectionRuntimeConflict("invalid collection runtime reservation identity")
    _validate_dispatch_digest_value(expected_dispatch_digest)
    if not isinstance(private_scope, PrivateTerminalCleanupAuthority):
        raise PrivateScopeRejected("a terminal reservation cleanup authority is required")
    with session_factory() as session, session.begin():
        attempt = _lock_terminal_cleanup_attempt(
            session,
            command_id,
            private_scope,
            private_binding,
            expected_dispatch_digest,
            cleanup_kind="RESERVATION_RELEASE",
        )
        if attempt.claim_token is not None or attempt.claim_expires_at is not None:
            raise CollectionRuntimeConflict("collection runtime reservation is claimed")
        attempt.state = "INVALIDATED"
        attempt.updated_at = _database_now(session)
        return _detach_after_flush(session, attempt)
