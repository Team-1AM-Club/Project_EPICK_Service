"""Synthetic private proposals tested against explicitly approved PostgreSQL."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from threading import Barrier, Event, Thread
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from tests.support.commit_gate_inspection import inspect_private_gate

from epick_engine.source_collection import private_scope as private_scope_module
from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateCommand,
    parse_commit_gate_command,
    staged_result_digest,
)
from epick_engine.source_collection.commit_gate_store import (
    CommitGateRejected,
    PrivateCommitGateAck,
    PrivateCommitStage,
    PrivateStagedOutbox,
    _hash,
    read_finalized_result,
)
from epick_engine.source_collection.commit_gate_store import (
    apply_commit_gate as _apply_commit_gate,
)
from epick_engine.source_collection.commit_gate_store import (
    stage_private_result as _stage_private_result,
)
from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult
from epick_engine.source_collection.persistence import (
    Base,
    CollectionRuntimeAttempt,
    Company,
    Evidence,
    OutboxEvent,
    PrivateDeletionOwnerState,
    PrivateDeletionProjectTombstone,
    Source,
    SourceObservation,
    SourcePolicyDecision,
    SourceVersion,
)
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateScopeRejected,
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
)
from epick_engine.source_collection.source_runtime_gate import (
    apply_collection_commit_gate as _apply_collection_commit_gate,
)
from epick_engine.source_collection.w1_private_authority_contracts import GateAuthorityResponse

pytestmark = pytest.mark.approved_postgres
NOW = datetime(2026, 9, 18, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    admin = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_w2_private_gate_{uuid4().hex}"
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
    epoch: int | None = None,
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
        raw["purge_owner_deletion_epoch"] = epoch or command.owner_deletion_epoch + 1
    return parse_commit_gate_command(raw)


def _trusted_scope(
    command: CollectionCommand,
    *,
    kind: str = "ACCOUNT",
    project_id: UUID | None = None,
    command_id: UUID | None = None,
) -> PrivateWriteScope:
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=command.authenticated_owner_ref,
            owner_deletion_epoch=command.owner_deletion_epoch,
            scope=PrivateDeletionScope(kind=kind, project_id=project_id),  # type: ignore[arg-type]
            authority_ref="w1:test-private-write-authority",
            command_id=command.command_id if command_id is None else command_id,
            job_id=command.job_id,
        )
    )


def _gate_authority_response(
    gate: CommitGateCommand,
    *,
    phase: str = "APPLY",
    kind: str = "ACCOUNT",
    project_id: UUID | None = None,
    **overrides: object,
) -> GateAuthorityResponse:
    raw: dict[str, object] = {
        "schema_version": "w1.private.w2-gate-authority.v1",
        "owner_user_id": str(gate.authenticated_owner_ref),
        "owner_deletion_epoch": gate.owner_deletion_epoch,
        "command_id": str(gate.command_id),
        "job_id": str(gate.job_id),
        "execution_fence": gate.execution_fence,
        "scope": (
            {"type": "ACCOUNT"}
            if kind == "ACCOUNT"
            else {"type": "PROJECT", "project_id": str(project_id)}
        ),
        "operation_id": str(gate.operation_id),
        "operation_revision": gate.operation_revision,
        "action": gate.action,
        "phase": phase,
        "result_digest": gate.result_digest,
        "purge_owner_deletion_epoch": gate.purge_owner_deletion_epoch,
        "authority_ref": "w1:test-gate-apply-authority",
    }
    raw.update(overrides)
    return GateAuthorityResponse.model_validate(raw)


def _trusted_gate_authority(
    gate: CommitGateCommand,
    *,
    kind: str = "ACCOUNT",
    project_id: UUID | None = None,
):
    return private_scope_module.PrivateGateAuthority.from_w1_response(
        _gate_authority_response(
            gate,
            kind=kind,
            project_id=project_id,
        )
    )


def _wait_for_postgres_lock(database_engine: Engine, backend_pid: int) -> None:
    deadline = monotonic() + 2.0
    with database_engine.connect() as connection:
        while monotonic() < deadline:
            if (
                connection.scalar(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": backend_pid},
                )
                == "Lock"
            ):
                return
            sleep(0.01)
    raise AssertionError("private gate transaction never waited on the owner lock")


def stage_private_result(
    session: Session,
    command: CollectionCommand,
    result: CollectionResult,
    **kwargs,
):
    kwargs.setdefault("private_scope", _trusted_scope(command))
    return _stage_private_result(session, command, result, **kwargs)


def apply_commit_gate(
    session: Session,
    gate: CommitGateCommand,
    **kwargs,
):
    if "private_gate_authority" not in kwargs:
        kwargs["private_gate_authority"] = _trusted_gate_authority(gate)
    return _apply_commit_gate(session, gate, **kwargs)


def apply_collection_commit_gate(
    session: Session,
    gate: CommitGateCommand,
    **kwargs,
):
    if "private_gate_authority" not in kwargs:
        kwargs["private_gate_authority"] = _trusted_gate_authority(gate)
    return _apply_collection_commit_gate(session, gate, **kwargs)


def test_stage_requires_exact_trusted_scope_and_persists_explicit_attribution(
    session_factory,
) -> None:
    account_command, account_result = _pair()
    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="scope"):
            _stage_private_result(
                session,
                account_command,
                account_result,
                message_id=uuid4(),
                occurred_at=NOW,
            )
        with pytest.raises(PrivateScopeRejected, match="command"):
            stage_private_result(
                session,
                account_command,
                account_result,
                message_id=uuid4(),
                occurred_at=NOW,
                private_scope=_trusted_scope(account_command, command_id=uuid4()),
            )
        stage_private_result(
            session,
            account_command,
            account_result,
            message_id=uuid4(),
            occurred_at=NOW,
            private_scope=_trusted_scope(account_command),
        )
        account_stage = session.get(PrivateCommitStage, account_command.command_id)
        assert account_stage.private_scope_kind == "ACCOUNT"
        assert account_stage.project_id is None

    project_id = uuid4()
    project_command, project_result = _pair()
    project_command = project_command.model_copy(update={"project_ref": str(project_id)})
    with session_factory.begin() as session:
        stage_private_result(
            session,
            project_command,
            project_result,
            message_id=uuid4(),
            occurred_at=NOW,
            private_scope=_trusted_scope(
                project_command,
                kind="PROJECT",
                project_id=project_id,
            ),
        )
        project_stage = session.get(PrivateCommitStage, project_command.command_id)
        assert project_stage.private_scope_kind == "PROJECT"
        assert project_stage.project_id == project_id


def test_gate_apply_requires_independent_exact_authority(session_factory) -> None:
    command, result = _pair()
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    with session_factory.begin() as session:
        stage_private_result(
            session,
            command,
            result,
            message_id=uuid4(),
            occurred_at=NOW,
        )

    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="gate authority"):
            _apply_commit_gate(
                session,
                gate,
                ack_message_id=uuid4(),
                occurred_at=NOW,
                private_gate_authority=_trusted_scope(command),
            )

    mutations = (
        {"operation_id": str(uuid4())},
        {"operation_revision": gate.operation_revision + 1},
        {"action": "ABORT"},
        {"result_digest": f"sha256:{'0' * 64}"},
        {"phase": "ACK_RELAY"},
        {"scope": {"type": "PROJECT", "project_id": str(uuid4())}},
    )
    for mutation in mutations:
        response = _gate_authority_response(gate, **mutation)
        decision = private_scope_module.PrivateGateAuthority.from_w1_response(response)
        with session_factory.begin() as session:
            with pytest.raises(PrivateScopeRejected, match="gate authority|scope binding"):
                _apply_commit_gate(
                    session,
                    gate,
                    ack_message_id=uuid4(),
                    occurred_at=NOW,
                    private_gate_authority=decision,
                )

    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="gate authority"):
            _apply_commit_gate(
                session,
                gate,
                ack_message_id=uuid4(),
                occurred_at=NOW,
                private_gate_authority=None,
            )

    with session_factory() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        assert stage is not None
        assert stage.state == "STAGED"
        assert stage.result_payload is not None
        assert session.query(PrivateCommitGateAck).count() == 0


@pytest.mark.parametrize("kind", ["ACCOUNT", "PROJECT"])
@pytest.mark.parametrize(
    ("purge_epoch", "accepted"),
    [(4, False), (5, True), (6, True)],
)
def test_stage_less_purge_epoch_boundary_creates_only_control_shell(
    session_factory,
    kind: str,
    purge_epoch: int,
    accepted: bool,
) -> None:
    command, result = _pair()
    project_id = uuid4() if kind == "PROJECT" else None
    gate = _gate(
        command,
        result,
        "PURGE",
        operation_id=uuid4(),
        revision=1,
        epoch=purge_epoch,
    )
    with session_factory.begin() as session:
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=command.authenticated_owner_ref,
                latest_epoch=5,
                account_deleted=kind == "ACCOUNT",
            )
        )
        if project_id is not None:
            session.add(
                PrivateDeletionProjectTombstone(
                    owner_user_id=command.authenticated_owner_ref,
                    project_id=project_id,
                    deletion_epoch=5,
                )
            )

    authority = _trusted_gate_authority(gate, kind=kind, project_id=project_id)
    if accepted:
        with session_factory.begin() as session:
            _apply_commit_gate(
                session,
                gate,
                ack_message_id=uuid4(),
                occurred_at=NOW,
                private_gate_authority=authority,
            )
    else:
        with session_factory.begin() as session:
            with pytest.raises(PrivateScopeRejected, match="regress"):
                _apply_commit_gate(
                    session,
                    gate,
                    ack_message_id=uuid4(),
                    occurred_at=NOW,
                    private_gate_authority=authority,
                )

    with session_factory() as session:
        owner = session.get(PrivateDeletionOwnerState, command.authenticated_owner_ref)
        assert owner is not None
        assert owner.latest_epoch == 5
        stage = session.get(PrivateCommitStage, command.command_id)
        if accepted:
            assert stage is not None
            assert stage.state == "PURGED"
            assert stage.result_payload is None
            assert stage.max_purge_epoch == str(purge_epoch)
        else:
            assert stage is None


def test_stage_less_abort_waits_for_owner_lock_before_creating_control_shell(
    database_engine: Engine,
    session_factory,
) -> None:
    command, result = _pair()
    gate = _gate(command, result, "ABORT", operation_id=uuid4(), revision=1)
    authority = _trusted_gate_authority(gate)
    with session_factory.begin() as session:
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=command.authenticated_owner_ref,
                latest_epoch=0,
                account_deleted=False,
            )
        )

    ready = Event()
    backend_pid: list[int] = []
    outcomes: list[str] = []
    errors: list[BaseException] = []

    def apply_after_owner_lock() -> None:
        try:
            with session_factory.begin() as session:
                backend_pid.append(int(session.scalar(text("SELECT pg_backend_pid()"))))
                ready.set()
                ack = _apply_commit_gate(
                    session,
                    gate,
                    ack_message_id=uuid4(),
                    occurred_at=NOW,
                    private_gate_authority=authority,
                )
                outcomes.append(ack.outcome)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    with session_factory() as owner_session:
        with owner_session.begin():
            owner = owner_session.scalar(
                select(PrivateDeletionOwnerState)
                .where(PrivateDeletionOwnerState.owner_user_id == command.authenticated_owner_ref)
                .with_for_update()
            )
            assert owner is not None
            owner.latest_epoch = 2
            worker = Thread(target=apply_after_owner_lock, daemon=True)
            worker.start()
            assert ready.wait(timeout=1)
            _wait_for_postgres_lock(database_engine, backend_pid[0])
            with session_factory() as observer:
                assert observer.get(PrivateCommitStage, command.command_id) is None

    worker.join(timeout=3)
    assert not worker.is_alive()
    assert errors == []
    assert outcomes == ["APPLIED"]
    with session_factory() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        owner = session.get(PrivateDeletionOwnerState, command.authenticated_owner_ref)
        assert stage is not None
        assert stage.state == "ABORTED"
        assert stage.result_payload is None
        assert owner is not None
        assert owner.latest_epoch == 2


def test_stage_rejects_stale_or_deleted_scope_but_historical_purge_is_allowed(
    session_factory,
) -> None:
    stale_command, stale_result = _pair()
    with session_factory.begin() as session:
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=stale_command.authenticated_owner_ref,
                latest_epoch=1,
                account_deleted=False,
            )
        )
    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="current epoch"):
            stage_private_result(
                session,
                stale_command,
                stale_result,
                message_id=uuid4(),
                occurred_at=NOW,
                private_scope=_trusted_scope(stale_command),
            )

    project_id = uuid4()
    deleted_command, deleted_result = _pair()
    deleted_command = deleted_command.model_copy(update={"project_ref": str(project_id)})
    with session_factory.begin() as session:
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=deleted_command.authenticated_owner_ref,
                latest_epoch=0,
                account_deleted=False,
            )
        )
        session.add(
            PrivateDeletionProjectTombstone(
                owner_user_id=deleted_command.authenticated_owner_ref,
                project_id=project_id,
                deletion_epoch=1,
            )
        )
    proof = _trusted_scope(deleted_command, kind="PROJECT", project_id=project_id)
    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="project tombstone"):
            stage_private_result(
                session,
                deleted_command,
                deleted_result,
                message_id=uuid4(),
                occurred_at=NOW,
                private_scope=proof,
            )
        purge = _gate(
            deleted_command,
            deleted_result,
            "PURGE",
            operation_id=uuid4(),
            revision=1,
        )
        apply_commit_gate(
            session,
            purge,
            ack_message_id=uuid4(),
            occurred_at=NOW,
            private_gate_authority=_trusted_gate_authority(
                purge,
                kind="PROJECT",
                project_id=project_id,
            ),
        )


def test_stage_prepare_are_invisible_and_finalize_is_owner_only(session_factory) -> None:
    command, result = _pair()
    operation_id = uuid4()
    with session_factory.begin() as session:
        proposal = stage_private_result(
            session, command, result, message_id=uuid4(), occurred_at=NOW
        )
        assert proposal.result == result
        assert (
            read_finalized_result(
                session, owner_ref=command.authenticated_owner_ref, command_id=command.command_id
            )
            is None
        )
        ack = apply_commit_gate(
            session,
            _gate(command, result, "PREPARE", operation_id=operation_id, revision=1),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
        assert ack.outcome == "APPLIED"
        assert (
            read_finalized_result(
                session, owner_ref=command.authenticated_owner_ref, command_id=command.command_id
            )
            is None
        )
    with session_factory.begin() as session:
        ack = apply_commit_gate(
            session,
            _gate(command, result, "FINALIZE", operation_id=operation_id, revision=2),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
        assert ack.outcome == "APPLIED"
    with session_factory() as session:
        assert (
            read_finalized_result(
                session, owner_ref=command.authenticated_owner_ref, command_id=command.command_id
            )
            == result
        )
        assert (
            read_finalized_result(session, owner_ref=uuid4(), command_id=command.command_id) is None
        )


def test_stage_kind_defaults_private_only_and_is_immutable_on_replay(session_factory) -> None:
    command, result = _pair()
    with session_factory.begin() as session:
        stage_private_result(session, command, result, message_id=uuid4(), occurred_at=NOW)
        assert session.get(PrivateCommitStage, command.command_id).stage_kind == "PRIVATE_ONLY"
        with pytest.raises(CommitGateRejected, match="binding mismatch"):
            stage_private_result(
                session,
                command,
                result,
                message_id=uuid4(),
                occurred_at=NOW,
                stage_kind="COLLECTION",
            )
        assert session.get(PrivateCommitStage, command.command_id).stage_kind == "PRIVATE_ONLY"


def _stage(session: Session, command: CollectionCommand, result: CollectionResult):
    return stage_private_result(session, command, result, message_id=uuid4(), occurred_at=NOW)


def _apply(session: Session, gate: CommitGateCommand, **kwargs):
    return apply_commit_gate(
        session,
        gate,
        ack_message_id=uuid4(),
        occurred_at=NOW,
        **kwargs,
    )


def _state(session: Session, command: CollectionCommand):
    return inspect_private_gate(
        session, owner_ref=command.authenticated_owner_ref, command_id=command.command_id
    )


@pytest.mark.parametrize("terminal", ["ABORT", "PURGE"])
def test_terminal_erases_private_payload_and_pending_stage_submission(session_factory, terminal):
    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        _stage(session, command, result)
        _apply(session, _gate(command, result, "PREPARE", operation_id=operation, revision=1))
        _apply(session, _gate(command, result, terminal, operation_id=operation, revision=7))
        state = _state(session, command)
        assert state["state"] == ("ABORTED" if terminal == "ABORT" else "PURGED")
        assert state["result_payload"] is None
        assert state["stage_payloads"] == [None]
        assert state["ack_count"] == 2
        assert (
            read_finalized_result(
                session, owner_ref=command.authenticated_owner_ref, command_id=command.command_id
            )
            is None
        )


@pytest.mark.parametrize("terminal", ["ABORT", "PURGE"])
def test_terminal_before_stage_leaves_tombstone_and_rejects_resurrection(session_factory, terminal):
    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        _apply(session, _gate(command, result, terminal, operation_id=operation, revision=10))
        before = _state(session, command)
        with pytest.raises(CommitGateRejected):
            _stage(session, command, result)
        with pytest.raises(CommitGateRejected):
            _apply(session, _gate(command, result, "FINALIZE", operation_id=operation, revision=11))
        with pytest.raises(CommitGateRejected):
            _apply(session, _gate(command, result, "PREPARE", operation_id=operation, revision=12))
        assert _state(session, command) == before
        assert before["stage_payloads"] == []
        assert before["result_payload"] is None


def test_terminal_before_stage_can_bind_collection_stage_kind(session_factory) -> None:
    command, result = _pair()
    with session_factory.begin() as session:
        apply_commit_gate(
            session,
            _gate(command, result, "ABORT", operation_id=uuid4(), revision=1),
            ack_message_id=uuid4(),
            occurred_at=NOW,
            missing_stage_kind="COLLECTION",
        )
        assert session.get(PrivateCommitStage, command.command_id).stage_kind == "COLLECTION"


@pytest.mark.parametrize("terminal", ["ABORT", "PURGE"])
def test_terminal_accepts_only_newer_revision_and_increasing_max_purge_epoch(
    session_factory, terminal
):
    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        _apply(
            session, _gate(command, result, terminal, operation_id=operation, revision=3, epoch=2)
        )
        ack = _apply(
            session, _gate(command, result, "PURGE", operation_id=operation, revision=99, epoch=8)
        )
        assert ack.outcome == "APPLIED"
        before = _state(session, command)
        for revision, epoch in [(100, 8), (100, 7), (98, 9), (99, 9)]:
            with pytest.raises((CommitGateRejected, PrivateScopeRejected)):
                _apply(
                    session,
                    _gate(
                        command,
                        result,
                        "PURGE",
                        operation_id=operation,
                        revision=revision,
                        epoch=epoch,
                    ),
                )
            assert _state(session, command) == before
        assert before["max_purge_epoch"] == 8
        assert before["operation_revision"] == 99


def test_exact_replay_and_new_message_same_revision_reuse_ack_without_reapply(session_factory):
    command, result = _pair()
    operation = uuid4()
    gate = _gate(command, result, "PREPARE", operation_id=operation, revision=4)
    with session_factory.begin() as session:
        staged = _stage(session, command, result)
        assert (
            stage_private_result(
                session, command, result, message_id=staged.message_id, occurred_at=NOW
            )
            == staged
        )
        ack = _apply(session, gate)
    # New session simulates crash after commit but before sending the ACK.
    with session_factory.begin() as session:
        assert _apply(session, gate) == ack
        alias = gate.model_copy(update={"message_id": uuid4()})
        assert _apply(session, alias) == ack
        assert _state(session, command)["ack_count"] == 1
        _apply(session, _gate(command, result, "PURGE", operation_id=operation, revision=20))
        assert _apply(session, gate) == ack
        assert _apply(session, alias) == ack
        assert _state(session, command)["state"] == "PURGED"
        assert _state(session, command)["stage_payloads"] == [None]


@pytest.mark.parametrize("replay_path", ["inbox", "receipt"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("occurred_at", "2026-09-18T00:00:01Z"),
        ("outcome", "DUPLICATE"),
    ],
)
def test_gate_replay_rejects_changed_persisted_ack_wire(
    session_factory,
    replay_path,
    field,
    value,
) -> None:
    command, result = _pair()
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    with session_factory.begin() as session:
        _stage(session, command, result)
        ack = _apply(session, gate)
    with session_factory.begin() as session:
        stored = session.get(PrivateCommitGateAck, ack.message_id)
        assert stored is not None
        stored.payload = {**stored.payload, field: value}

    replay = gate if replay_path == "inbox" else gate.model_copy(update={"message_id": uuid4()})
    with session_factory.begin() as session:
        with pytest.raises(CommitGateRejected, match="invalid persisted private commit-gate ACK"):
            _apply(session, replay)


def test_exact_inbox_replay_rejects_a_different_trusted_scope(session_factory) -> None:
    command, result = _pair()
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1)
    with session_factory.begin() as session:
        _stage(session, command, result)
        _apply(session, gate)

    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="scope binding"):
            _apply_commit_gate(
                session,
                gate,
                ack_message_id=uuid4(),
                occurred_at=NOW,
                private_gate_authority=_trusted_gate_authority(
                    gate,
                    kind="PROJECT",
                    project_id=UUID("00000000-0000-4000-8000-000000000299"),
                ),
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("authenticated_owner_ref", UUID("00000000-0000-4000-8000-000000000002")),
        ("job_id", UUID("00000000-0000-4000-8000-000000000003")),
        ("execution_fence", 8),
        ("owner_deletion_epoch", 8),
        ("result_digest", "sha256:" + "f" * 64),
        ("operation_id", UUID("00000000-0000-4000-8000-000000000004")),
        ("operation_revision", 1),
        ("action", "FINALIZE"),
    ],
)
def test_binding_mutation_stale_and_same_revision_other_action_have_no_effect(
    session_factory, field, value
):
    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        _stage(session, command, result)
        _apply(session, _gate(command, result, "PREPARE", operation_id=operation, revision=2))
        before = _state(session, command)
        gate = _gate(command, result, "PREPARE", operation_id=operation, revision=2)
        expected_error = (
            (CommitGateRejected, PrivateScopeRejected)
            if field == "owner_deletion_epoch"
            else CommitGateRejected
        )
        with pytest.raises(expected_error):
            _apply(session, gate.model_copy(update={field: value}))
        assert _state(session, command) == before


def test_message_id_mutation_and_stage_payload_mutation_are_rejected(session_factory):
    command, result = _pair()
    operation = uuid4()
    gate = _gate(command, result, "PREPARE", operation_id=operation, revision=1)
    with session_factory.begin() as session:
        staged = _stage(session, command, result)
        _apply(session, gate)
        before = _state(session, command)
        for changed in [
            gate.model_copy(update={"operation_revision": 3}),
            gate.model_copy(update={"issued_at": datetime(2026, 9, 19, tzinfo=UTC)}),
            gate.model_copy(update={"execution_fence": True}),
        ]:
            with pytest.raises(CommitGateRejected):
                _apply(
                    session,
                    changed,
                    private_gate_authority=(
                        _trusted_gate_authority(gate)
                        if changed.execution_fence is True
                        else _trusted_gate_authority(changed)
                    ),
                )
            assert _state(session, command) == before
        with pytest.raises(CommitGateRejected):
            stage_private_result(
                session,
                command,
                result.model_copy(update={"message_ko": "변조 합성 결과"}),
                message_id=staged.message_id,
                occurred_at=NOW,
            )
        with pytest.raises(CommitGateRejected):
            stage_private_result(
                session,
                command,
                result,
                message_id=staged.message_id,
                occurred_at=datetime(2026, 9, 19, tzinfo=UTC),
            )
        assert _state(session, command) == before


def test_finalize_requires_prepared_and_abort_cannot_undo_finalized(session_factory):
    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        _stage(session, command, result)
        before = _state(session, command)
        with pytest.raises(CommitGateRejected):
            _apply(session, _gate(command, result, "FINALIZE", operation_id=operation, revision=1))
        assert _state(session, command) == before
        _apply(session, _gate(command, result, "PREPARE", operation_id=operation, revision=5))
        _apply(session, _gate(command, result, "FINALIZE", operation_id=operation, revision=12))
        finalized = _state(session, command)
        with pytest.raises(CommitGateRejected):
            _apply(session, _gate(command, result, "ABORT", operation_id=operation, revision=13))
        assert _state(session, command) == finalized


def test_caller_rollback_removes_stage_and_gate_ack_without_internal_commit(session_factory):
    command, result = _pair()
    with session_factory() as session:
        _stage(session, command, result)
        _apply(session, _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1))
        session.rollback()
    with session_factory() as session:
        assert _state(session, command) is None


def test_collection_finalize_invariant_uses_one_savepoint_for_private_gate_and_candidate(
    session_factory,
) -> None:
    """A rejected collection FINALIZE must not leave a private ACK after caller commit."""

    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        stage_private_result(
            session,
            command,
            result,
            message_id=uuid4(),
            occurred_at=NOW,
            stage_kind="COLLECTION",
        )
        _apply(session, _gate(command, result, "PREPARE", operation_id=operation, revision=1))

    with session_factory() as session:
        before = _state(session, command)
        with pytest.raises((CommitGateRejected, RuntimeError), match="candidate"):
            apply_collection_commit_gate(
                session,
                _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        # This deliberately commits the caller-owned outer transaction.  The
        # rejected FINALIZE must still have rolled back its private gate state.
        session.commit()

    with session_factory() as session:
        assert _state(session, command) == before


@pytest.mark.parametrize(
    "field",
    [
        "authenticated_owner_ref",
        "job_id",
        "execution_fence",
        "owner_deletion_epoch",
        "result_digest",
    ],
)
def test_collection_finalize_rejects_each_gate_to_stage_binding_mismatch_without_ack(
    session_factory,
    field: str,
) -> None:
    """The collection wrapper must preserve every private gate/stage binding check."""

    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        stage_private_result(
            session,
            command,
            result,
            message_id=uuid4(),
            occurred_at=NOW,
            stage_kind="COLLECTION",
        )
        _apply(session, _gate(command, result, "PREPARE", operation_id=operation, revision=1))

    values = {
        "authenticated_owner_ref": uuid4(),
        "job_id": uuid4(),
        "execution_fence": int(command.execution_fence) + 1,
        "owner_deletion_epoch": command.owner_deletion_epoch + 1,
        "result_digest": "sha256:" + "f" * 64,
    }
    with session_factory() as session:
        before = _state(session, command)
        gate = _gate(command, result, "FINALIZE", operation_id=operation, revision=2).model_copy(
            update={field: values[field]}
        )
        expected_error = (
            (CommitGateRejected, PrivateScopeRejected)
            if field == "owner_deletion_epoch"
            else CommitGateRejected
        )
        expected_message = (
            "binding mismatch|current epoch"
            if field == "owner_deletion_epoch"
            else "binding mismatch"
        )
        with pytest.raises(expected_error, match=expected_message):
            apply_collection_commit_gate(
                session,
                gate,
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        session.commit()

    with session_factory() as session:
        assert _state(session, command) == before


def _persisted_collection_stage(session_factory):
    """Create a real Task 4 candidate/public stage for FINALIZE-only checks."""

    from tests.integration.source_collection.test_collection_runtime_storage import (
        _claimed_candidate_inputs,
        commit_collection_candidate,
    )

    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(session_factory)
    proposal = commit_collection_candidate(
        session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    command = dispatch.payload
    operation = uuid4()
    with session_factory.begin() as session:
        _apply(
            session, _gate(command, proposal.result, "PREPARE", operation_id=operation, revision=1)
        )
    return command, proposal.result, operation


def test_collection_gate_rejects_candidate_to_stage_scope_mismatch_without_ack(
    session_factory,
) -> None:
    command, result, operation = _persisted_collection_stage(session_factory)
    other_project_id = UUID("00000000-0000-4000-8000-000000000299")
    with session_factory.begin() as session:
        candidate = session.get(CollectionRuntimeAttempt, command.command_id)
        assert candidate is not None
        candidate.private_scope_kind = "PROJECT"
        candidate.project_id = other_project_id

    with session_factory() as session:
        with pytest.raises(PrivateScopeRejected, match="candidate private scope"):
            apply_collection_commit_gate(
                session,
                _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        session.commit()

    with session_factory() as session:
        candidate = session.get(CollectionRuntimeAttempt, command.command_id)
        assert candidate is not None
        assert candidate.state == "PERSISTED"
        assert candidate.private_scope_kind == "PROJECT"
        assert candidate.project_id == other_project_id
        state = _state(session, command)
        assert state["state"] == "PREPARED"
        assert state["ack_count"] == 1


def _mutate_collection_stage_proposal(
    session: Session,
    command: CollectionCommand,
    result: CollectionResult,
    operation: UUID,
    *,
    field: str,
) -> CommitGateCommand:
    stage = session.get(PrivateCommitStage, command.command_id)
    outbox = session.scalar(
        select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
    )
    assert stage is not None and outbox is not None and outbox.payload is not None
    raw = json.loads(json.dumps(outbox.payload))
    replacement = uuid4()
    if field == "command_id":
        raw["command"]["command_id"] = str(replacement)
        raw["result"]["command_id"] = str(replacement)
    elif field == "owner":
        raw["command"]["authenticated_owner_ref"] = str(replacement)
    elif field == "job":
        raw["command"]["job_id"] = str(replacement)
        raw["result"]["job_id"] = str(replacement)
    elif field == "source":
        raw["command"]["source_id"] = str(replacement)
        raw["result"]["source_id"] = str(replacement)
        for source_ref in raw["result"].get("successful_source_refs", []):
            source_ref["source_id"] = str(replacement)
    elif field == "company":
        raw["command"]["company_id"] = str(replacement)
    else:  # pragma: no cover - parametrization is the contract surface.
        raise AssertionError(f"unexpected binding field: {field}")

    proposal_command = CollectionCommand.model_validate(raw["command"])
    proposal_result = CollectionResult.model_validate(raw["result"])
    digest = staged_result_digest(proposal_command, proposal_result)
    raw["result_digest"] = digest
    outbox.payload = raw
    outbox.wire_hash = _hash(raw)
    stage.result_payload = proposal_result.model_dump(mode="json")
    stage.result_digest = digest
    stage.owner_ref = proposal_command.authenticated_owner_ref
    stage.job_id = proposal_command.job_id
    stage.execution_fence = proposal_command.execution_fence
    stage.owner_deletion_epoch = str(proposal_command.owner_deletion_epoch)
    return _gate(command, result, "FINALIZE", operation_id=operation, revision=2).model_copy(
        update={
            "authenticated_owner_ref": proposal_command.authenticated_owner_ref,
            "job_id": proposal_command.job_id,
            "result_digest": digest,
        }
    )


@pytest.mark.parametrize("field", ["command_id", "owner", "job", "source", "company"])
def test_collection_finalize_rejects_every_proposal_to_candidate_identity_mismatch(
    session_factory,
    field: str,
) -> None:
    """A syntactically valid stage proposal may not bind to another candidate identity."""

    command, result, operation = _persisted_collection_stage(session_factory)
    with session_factory.begin() as session:
        gate = _mutate_collection_stage_proposal(
            session,
            command,
            result,
            operation,
            field=field,
        )

    with session_factory() as session:
        with pytest.raises(CommitGateRejected, match="proposal candidate binding"):
            apply_collection_commit_gate(
                session,
                gate,
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        session.commit()

    with session_factory() as session:
        state = inspect_private_gate(
            session,
            owner_ref=gate.authenticated_owner_ref,
            command_id=command.command_id,
        )
        assert state["state"] == "PREPARED"
        assert state["ack_count"] == 1


def test_collection_finalize_rejects_corrupt_staged_proposal_without_ack(session_factory) -> None:
    """A malformed durable COLLECTION proposal cannot be finalized by trusting JSONB."""

    command, result, operation = _persisted_collection_stage(session_factory)
    with session_factory.begin() as session:
        outbox = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert outbox is not None
        outbox.payload = {"schema_version": "w2.private.staged-result.proposal.v1"}

    with session_factory() as session:
        with pytest.raises((CommitGateRejected, RuntimeError), match="staged|proposal|payload"):
            apply_collection_commit_gate(
                session,
                _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        session.commit()

    with session_factory() as session:
        state = _state(session, command)
        assert state["state"] == "PREPARED"
        assert state["ack_count"] == 1


def _copy_source_version(version: SourceVersion, *, policy_decision_id: UUID) -> SourceVersion:
    return SourceVersion(
        source_version_id=uuid4(),
        source_id=version.source_id,
        company_id=version.company_id,
        title=version.title,
        source_type=version.source_type,
        canonical_url=version.canonical_url,
        content_hash="f" * 64,
        hash_profile_version=version.hash_profile_version,
        representation=version.representation,
        first_parser_version=version.first_parser_version,
        collected_at=version.collected_at,
        published_at=version.published_at,
        valid_from=version.valid_from,
        valid_to=version.valid_to,
        language=version.language,
        policy_decision_id=policy_decision_id,
    )


def _next_policy(source_id: UUID, revision: int) -> SourcePolicyDecision:
    return SourcePolicyDecision(
        policy_decision_id=uuid4(),
        source_id=source_id,
        revision=revision,
        official_status="verified",
        access_class="public",
        collection_permission="allowed",
        excerpt_storage_permission="allowed",
        body_storage_permission="denied",
        redistribution_permission="unknown",
        evidence_refs=[f"synthetic:policy:{revision}"],
        checked_at=NOW,
        policy_version=f"policy-{revision}",
    )


@pytest.mark.parametrize("mismatch", ["observation_version", "result_version", "version_policy"])
def test_collection_finalize_rejects_candidate_result_fk_or_policy_mismatch(
    session_factory,
    mismatch: str,
) -> None:
    """FINALIZE never promotes a candidate whose public result identity/policy changed."""

    command, result, operation = _persisted_collection_stage(session_factory)
    with session_factory.begin() as session:
        attempt = session.get(CollectionRuntimeAttempt, command.command_id)
        assert attempt is not None
        observation = session.get(SourceObservation, attempt.observation_id)
        version = session.get(SourceVersion, attempt.source_version_id)
        assert observation is not None and version is not None
        gate = _gate(command, result, "FINALIZE", operation_id=operation, revision=2)

        if mismatch == "observation_version":
            replacement_observation_id = uuid4()
            session.add(
                SourceObservation(
                    observation_id=replacement_observation_id,
                    source_id=observation.source_id,
                    source_version_id=None,
                    policy_decision_id=observation.policy_decision_id,
                    observed_at=observation.observed_at,
                    access_class=observation.access_class,
                    acquisition_status=observation.acquisition_status,
                    http_status=observation.http_status,
                    checked_url=observation.checked_url,
                    retrieval_validator=observation.retrieval_validator,
                    error_code=observation.error_code,
                    representation=observation.representation,
                )
            )
            session.flush()
            attempt.observation_id = replacement_observation_id
        elif mismatch == "result_version":
            replacement = _copy_source_version(
                version,
                policy_decision_id=version.policy_decision_id,
            )
            session.add(replacement)
            session.flush()
            stage = session.get(PrivateCommitStage, command.command_id)
            outbox = session.scalar(
                select(PrivateStagedOutbox).where(
                    PrivateStagedOutbox.command_id == command.command_id
                )
            )
            assert stage is not None and outbox is not None and outbox.payload is not None
            raw = json.loads(json.dumps(outbox.payload))
            raw["result"]["successful_source_refs"][0]["source_version_id"] = str(
                replacement.source_version_id
            )
            proposal_command = CollectionCommand.model_validate(raw["command"])
            proposal_result = CollectionResult.model_validate(raw["result"])
            digest = staged_result_digest(proposal_command, proposal_result)
            raw["result_digest"] = digest
            outbox.payload = raw
            outbox.wire_hash = _hash(raw)
            stage.result_payload = proposal_result.model_dump(mode="json")
            stage.result_digest = digest
            gate = gate.model_copy(update={"result_digest": digest})
        elif mismatch == "version_policy":
            policy = _next_policy(command.source_id, revision=4)
            session.add(policy)
            session.flush()
            version.policy_decision_id = policy.policy_decision_id
        else:  # pragma: no cover - parametrization is the contract surface.
            raise AssertionError(f"unexpected mismatch: {mismatch}")

    with session_factory() as session:
        with pytest.raises(
            CommitGateRejected,
            match="observation|version|policy binding|successful version binding",
        ):
            apply_collection_commit_gate(
                session,
                gate,
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        session.commit()

    with session_factory() as session:
        state = _state(session, command)
        assert state["state"] == "PREPARED"
        assert state["ack_count"] == 1


def test_fault_after_state_change_before_ack_flush_rolls_back_everything(session_factory):
    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        _stage(session, command, result)
        before = _state(session, command)

    def fail_ack_insert(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO") and "private_commit_gate_acks" in statement:
            raise RuntimeError("synthetic ACK write fault")

    engine = session_factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", fail_ack_insert)
    try:
        with pytest.raises(RuntimeError, match="synthetic ACK write fault"):
            with session_factory.begin() as session:
                _apply(
                    session, _gate(command, result, "PREPARE", operation_id=operation, revision=1)
                )
    finally:
        event.remove(engine, "before_cursor_execute", fail_ack_insert)
    with session_factory() as session:
        assert _state(session, command) == before


def test_concurrent_first_stage_serializes_absent_row_and_returns_one_submission(session_factory):
    command, result = _pair()
    barrier = Barrier(2)

    def submit():
        with session_factory.begin() as session:
            barrier.wait(timeout=10)
            return _stage(session, command, result)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: submit(), range(2)))
    assert first == second
    with session_factory() as session:
        assert _state(session, command)["stage_count"] == 1


@pytest.mark.parametrize("race", ["stage_purge", "finalize_purge"])
def test_purge_race_finishes_terminal_without_resurrection(session_factory, race):
    command, result = _pair()
    operation = uuid4()
    if race == "finalize_purge":
        with session_factory.begin() as session:
            _stage(session, command, result)
            _apply(session, _gate(command, result, "PREPARE", operation_id=operation, revision=1))
    barrier = Barrier(2)

    def execute(kind):
        try:
            with session_factory.begin() as session:
                barrier.wait(timeout=10)
                if kind == "PURGE":
                    return _apply(
                        session,
                        _gate(
                            command, result, "PURGE", operation_id=operation, revision=99, epoch=5
                        ),
                    ).outcome
                if race == "stage_purge":
                    _stage(session, command, result)
                else:
                    _apply(
                        session,
                        _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
                    )
                return "APPLIED"
        except CommitGateRejected:
            return "REJECTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(execute, ["other", "PURGE"]))
    assert outcomes[1] == "APPLIED"
    assert outcomes[0] in {"APPLIED", "REJECTED"}
    with session_factory() as session:
        state = _state(session, command)
        assert state["state"] == "PURGED"
        assert state["result_payload"] is None
        assert all(payload is None for payload in state["stage_payloads"])


def test_purge_preserves_other_owner_and_all_public_table_rows(session_factory):
    first, first_result = _pair()
    second, second_result = _pair()
    first_operation, second_operation = uuid4(), uuid4()
    with session_factory.begin() as session:
        company = Company(
            company_id=uuid4(),
            legal_name="Synthetic Gate Company",
            aliases=[],
            official_domains=["example.test"],
            legal_identifiers={},
            identity_status="verified",
            identity_evidence=["synthetic://gate-test"],
        )
        source = Source(
            source_id=uuid4(),
            company_id=company.company_id,
            source_type="company_website",
            canonical_url="https://example.test/gate-public",
            title="Synthetic Public Source",
        )
        session.add_all([company, source])
        session.flush()
        policy = SourcePolicyDecision(
            policy_decision_id=uuid4(),
            source_id=source.source_id,
            revision=1,
            official_status="verified",
            access_class="public",
            collection_permission="allowed",
            excerpt_storage_permission="allowed",
            body_storage_permission="denied",
            redistribution_permission="unknown",
            evidence_refs=["synthetic://gate-public-policy"],
            checked_at=NOW,
            policy_version="synthetic-gate-policy-v1",
        )
        session.add(policy)
        session.flush()
        unknown = {"status": "unknown", "raw_text": None, "value": None}
        version = SourceVersion(
            source_version_id=uuid4(),
            source_id=source.source_id,
            company_id=company.company_id,
            title=source.title,
            source_type=source.source_type,
            canonical_url=source.canonical_url,
            content_hash="a" * 64,
            hash_profile_version="synthetic-gate-html-v1",
            representation="html",
            first_parser_version="synthetic-gate-parser-v1",
            collected_at=NOW,
            published_at=unknown,
            valid_from=unknown,
            valid_to=unknown,
            language="ko",
            policy_decision_id=policy.policy_decision_id,
        )
        session.add(version)
        session.flush()
        session.add_all(
            [
                Evidence(
                    evidence_id=uuid4(),
                    source_version_id=version.source_version_id,
                    evidence_key="synthetic-gate-public:0",
                    section_title="합성 공용 자료",
                    text_excerpt="합성 공용 기업 근거",
                    locator={"kind": "css", "value": "#public"},
                    chunk_order=0,
                    origin_kind="direct",
                ),
                OutboxEvent(
                    event_id=uuid4(),
                    aggregate_id=source.source_id,
                    aggregate_revision=1,
                    event_type="source.version.available",
                    schema_version="w2.source.v1",
                    payload={"synthetic": "public-source-outbox"},
                    occurred_at=NOW,
                    delivery_state="pending",
                ),
            ]
        )
        session.flush()
        for command, result, operation in [
            (first, first_result, first_operation),
            (second, second_result, second_operation),
        ]:
            _stage(session, command, result)
            _apply(session, _gate(command, result, "PREPARE", operation_id=operation, revision=1))
            _apply(session, _gate(command, result, "FINALIZE", operation_id=operation, revision=2))
        other_before = _state(session, second)
        schema = session.connection().get_execution_options()["schema_translate_map"][None]
        public_tables = [name for name in Base.metadata.tables if not name.startswith("private_")]

        def snapshot():
            return {
                name: session.execute(
                    text(
                        f'SELECT to_jsonb(public_row) FROM "{schema}"."{name}" public_row '
                        "ORDER BY to_jsonb(public_row)::text"
                    )
                )
                .scalars()
                .all()
                for name in public_tables
            }

        public_before = snapshot()
        _apply(
            session, _gate(first, first_result, "PURGE", operation_id=first_operation, revision=9)
        )
        assert _state(session, second) == other_before
        assert (
            read_finalized_result(
                session, owner_ref=second.authenticated_owner_ref, command_id=second.command_id
            )
            == second_result
        )
        assert snapshot() == public_before
        assert public_before["sources"][0]["title"] == "Synthetic Public Source"
        assert len(public_before["source_versions"]) == 1
        assert len(public_before["evidence"]) == 1
        assert len(public_before["outbox_events"]) == 1


def test_unique_ack_identity_conflict_rolls_back_state_and_preserves_caller_transaction(
    session_factory,
):
    command, result = _pair()
    operation, ack_id = uuid4(), uuid4()
    with session_factory.begin() as session:
        _stage(session, command, result)
        apply_commit_gate(
            session,
            _gate(command, result, "PREPARE", operation_id=operation, revision=1),
            ack_message_id=ack_id,
            occurred_at=NOW,
        )
        before = _state(session, command)
        with pytest.raises(CommitGateRejected, match="identity conflict"):
            apply_commit_gate(
                session,
                _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
                ack_message_id=ack_id,
                occurred_at=NOW,
            )
        assert _state(session, command) == before
    with session_factory() as session:
        assert _state(session, command) == before


@pytest.mark.parametrize("identity", ["stage_message", "gate_message", "operation"])
def test_id_reuse_across_commands_does_not_mutate_either_owner(session_factory, identity):
    first, first_result = _pair()
    second, second_result = _pair()
    operation, second_operation = uuid4(), uuid4()
    stage_id = uuid4()
    first_gate = _gate(first, first_result, "PREPARE", operation_id=operation, revision=1)
    with session_factory.begin() as session:
        stage_private_result(session, first, first_result, message_id=stage_id, occurred_at=NOW)
        _apply(session, first_gate)
        _stage(session, second, second_result)
        before_first, before_second = _state(session, first), _state(session, second)
        with pytest.raises(CommitGateRejected):
            if identity == "stage_message":
                stage_private_result(
                    session, second, second_result, message_id=stage_id, occurred_at=NOW
                )
            else:
                second_gate = _gate(
                    second,
                    second_result,
                    "PREPARE",
                    operation_id=operation if identity == "operation" else second_operation,
                    revision=2,
                )
                if identity == "gate_message":
                    second_gate = second_gate.model_copy(
                        update={"message_id": first_gate.message_id}
                    )
                _apply(session, second_gate)
        assert _state(session, first) == before_first
        assert _state(session, second) == before_second


def test_concurrent_duplicate_gate_has_one_ack_and_exact_replay(session_factory):
    command, result = _pair()
    gate = _gate(command, result, "PREPARE", operation_id=uuid4(), revision=10)
    with session_factory.begin() as session:
        _stage(session, command, result)
    barrier = Barrier(2)

    def apply():
        with session_factory.begin() as session:
            barrier.wait(timeout=10)
            return _apply(session, gate)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: apply(), range(2)))
    assert first == second
    with session_factory() as session:
        assert _state(session, command)["ack_count"] == 1


def test_private_scope_rejects_wire_epoch_beyond_signed_bigint(session_factory):
    command, result = _pair()
    huge = 2**100
    command = command.model_copy(
        update={"execution_fence": str(huge), "owner_deletion_epoch": huge}
    )
    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="signed 64-bit"):
            _stage(session, command, result)
        assert _state(session, command) is None


def test_jsonb_nul_rejection_hides_private_data_and_preserves_caller_transaction(session_factory):
    command, result = _pair()
    canary = "SYNTHETIC_PRIVATE_JSONB_CANARY"
    invalid_result = result.model_copy(update={"message_ko": canary + "\x00"})
    with session_factory.begin() as session:
        try:
            _stage(session, command, invalid_result)
        except Exception as error:
            is_safe_rejection = isinstance(error, CommitGateRejected)
            disclosed_in_exception = canary in str(error)
            disclosed_in_traceback = canary in "".join(traceback.format_exception(error))
            error_class = type(error).__name__
        else:
            pytest.fail("PostgreSQL JSONB-incompatible private input was accepted")
        assert is_safe_rejection, error_class
        assert not disclosed_in_exception
        assert not disclosed_in_traceback
        assert _state(session, command) is None
        # The caller catches the rejection and continues the same root transaction.
        assert _stage(session, command, result).result == result
        _apply(session, _gate(command, result, "PREPARE", operation_id=uuid4(), revision=1))
    with session_factory() as session:
        assert _state(session, command)["state"] == "PREPARED"
        assert _state(session, command)["ack_count"] == 1


@pytest.mark.parametrize("error_type", [TypeError, ValueError])
def test_json_serialization_rejection_hides_private_data_and_preserves_caller_transaction(
    database_engine, error_type
):
    command, result = _pair()
    canary = "SYNTHETIC_PRIVATE_SERIALIZER_CANARY"
    invalid_result = result.model_copy(update={"message_ko": canary})

    engine = create_engine(
        database_engine.url,
        json_serializer=partial(_serialize_with_fault, error_type=error_type, canary=canary),
    ).execution_options(
        schema_translate_map=database_engine.get_execution_options()["schema_translate_map"]
    )
    try:
        with Session(engine) as session, session.begin():
            try:
                _stage(session, command, invalid_result)
            except Exception as error:
                is_safe_rejection = isinstance(error, CommitGateRejected)
                disclosed_in_exception = canary in str(error)
                disclosed_in_traceback = canary in "".join(traceback.format_exception(error))
                error_class = type(error).__name__
            else:
                pytest.fail("Private JSON serialization failure was accepted")
            assert is_safe_rejection, error_class
            assert not disclosed_in_exception
            assert not disclosed_in_traceback
            assert _state(session, command) is None
            assert _stage(session, command, result).result == result
    finally:
        engine.dispose()


def _serialize_with_fault(value, *, error_type, canary):
    if isinstance(value, dict) and value.get("message_ko") == canary:
        raise error_type(canary)
    return json.dumps(value)


def test_postgres_statement_rejection_hides_private_data_and_preserves_caller_transaction(
    session_factory,
):
    command, result = _pair()
    canary = "SYNTHETIC_PRIVATE_STATEMENT_CANARY"
    interrupted_result = result.model_copy(update={"message_ko": canary})
    engine = session_factory.kw["bind"]
    schema = engine.get_execution_options()["schema_translate_map"][None]
    with engine.begin() as connection:
        connection.execute(
            text(
                f'CREATE FUNCTION "{schema}".synthetic_private_write_fault() RETURNS trigger '
                "LANGUAGE plpgsql AS $fault$ BEGIN RAISE EXCEPTION "
                "'synthetic private statement interruption' USING ERRCODE = '57014'; END $fault$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER synthetic_private_write_fault BEFORE INSERT ON "
                f'"{schema}".private_commit_stages '
                f'FOR EACH ROW EXECUTE FUNCTION "{schema}".synthetic_private_write_fault()'
            )
        )
    with session_factory.begin() as session:
        try:
            _stage(session, command, interrupted_result)
        except Exception as error:
            is_safe_rejection = isinstance(error, CommitGateRejected)
            disclosed_in_exception = canary in str(error)
            disclosed_in_traceback = canary in "".join(traceback.format_exception(error))
            error_class = type(error).__name__
        else:
            pytest.fail("Interrupted private statement was accepted")
        assert is_safe_rejection, error_class
        assert not disclosed_in_exception
        assert not disclosed_in_traceback
        assert _state(session, command) is None
        session.execute(
            text(f'DROP TRIGGER synthetic_private_write_fault ON "{schema}".private_commit_stages')
        )
        assert _stage(session, command, result).result == result


def test_migration_upgrade_matches_private_metadata_in_isolated_postgres(approved_postgres_url):
    root = Path(__file__).resolve().parents[3]
    scripts = ScriptDirectory.from_config(Config(root / "alembic.ini"))
    assert scripts.get_heads() == ["0013_deletion_ack_confirmed"]
    admin = create_engine(approved_postgres_url)
    schema = f"epick_w2_gate_migration_{uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        scoped_url = approved_postgres_url.set(
            query={**approved_postgres_url.query, "options": f"-csearch_path={schema}"}
        )
        environment = {
            **os.environ,
            "EPICK_DATABASE_URL": scoped_url.render_as_string(hide_password=False),
        }
        for target in ["0005_private_gate_delivery", "head"]:
            completed = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", target],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Do not include a DSN or driver parameters in failure diagnostics.
            assert completed.returncode == 0, f"isolated Alembic upgrade to {target} failed"
        with admin.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0013_deletion_ack_confirmed")
            private_names = {name for name in Base.metadata.tables if name.startswith("private_")}

            def include_object(obj, name, type_, reflected, compare_to):
                return name in private_names if type_ == "table" else True

            comparison = MigrationContext.configure(
                connection, opts={"include_object": include_object}
            )
            assert compare_metadata(comparison, Base.metadata) == []
            with pytest.raises(RuntimeError, match="Destructive downgrade"):
                scripts.get_revision("0004_private_commit_gate").module.downgrade()
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
