"""Count-only CT15 inspection for two explicit private command scopes."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from epick_engine.source_collection.commit_gate_contracts import StagedResultProposal
from epick_engine.source_collection.commit_gate_runtime import _strict_json_object
from epick_engine.source_collection.commit_gate_store import (
    PrivateCommitGateAck,
    PrivateCommitStage,
    PrivateStagedOutbox,
    _lock_command,
)
from epick_engine.source_collection.contracts import ContractModel
from epick_engine.source_collection.w1_transport import (
    W1WireContractError,
    _parse_wire,
    _revalidate_model,
)

MAX_RUN_SCOPE_BYTES = 4_096
CT15_RUN_ID_PATTERN = r"^ct15-[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$"
_STATES = ("STAGED", "PREPARED", "FINALIZED", "ABORTED", "PURGED")
_COUNT_KEYS = (
    "result_payload_count",
    "visible_result_count",
    "staged_outbox_count",
    "staged_payload_count",
    "pending_staged_count",
    "ack_count",
    "pending_ack_count",
)

type SourceVerification = Literal["matched", "absent", "erased_unverifiable"]


class Ct15InspectionError(ValueError):
    """Fixed operator diagnostic that never embeds private scope or stored data."""


class Ct15InspectionBinding(ContractModel):
    owner_ref: UUID
    job_id: UUID
    command_id: UUID


class Ct15RunInspectionScope(ContractModel):
    schema_version: Literal["w2.ct15.run-inspection.scope.v1"]
    run_id: Annotated[str, Field(pattern=CT15_RUN_ID_PATTERN)]
    source_id: UUID
    primary: Ct15InspectionBinding
    secondary: Ct15InspectionBinding

    @model_validator(mode="after")
    def validate_distinct_scopes(self) -> Ct15RunInspectionScope:
        if (
            self.primary.owner_ref == self.secondary.owner_ref
            or self.primary.job_id == self.secondary.job_id
            or self.primary.command_id == self.secondary.command_id
        ):
            raise ValueError("primary and secondary CT15 scopes must be distinct")
        return self


def parse_run_scope(payload: object) -> Ct15RunInspectionScope:
    """Parse the minimal explicit run binding without exposing validation inputs."""

    try:
        if isinstance(payload, Ct15RunInspectionScope):
            return _revalidate_model(
                payload,
                Ct15RunInspectionScope,
                label="CT15 run inspection scope",
            )
        return _parse_wire(
            payload,
            Ct15RunInspectionScope,
            label="CT15 run inspection scope",
        )
    except W1WireContractError:
        raise Ct15InspectionError("invalid CT15 run inspection scope") from None


def load_run_scope(path: Path) -> Ct15RunInspectionScope:
    """Load one strict, size-bounded JSON fixture from an operator-selected path."""

    try:
        if not isinstance(path, Path):
            raise Ct15InspectionError("invalid CT15 run inspection scope")
        with path.open("rb") as fixture:
            data = fixture.read(MAX_RUN_SCOPE_BYTES + 1)
        if len(data) > MAX_RUN_SCOPE_BYTES:
            raise Ct15InspectionError("CT15 run inspection scope exceeds bound")
        payload = _strict_json_object(data.decode("utf-8"), max_bytes=MAX_RUN_SCOPE_BYTES)
        return parse_run_scope(payload)
    except Ct15InspectionError:
        raise
    except (OSError, UnicodeDecodeError, ValueError):
        raise Ct15InspectionError("invalid CT15 run inspection scope") from None


def _stored_source_verification(
    session: Session,
    binding: Ct15InspectionBinding,
    source_id: UUID,
    row: PrivateCommitStage | None,
) -> SourceVerification:
    if row is None:
        return "absent"
    if row.owner_ref != binding.owner_ref or row.job_id != binding.job_id:
        raise Ct15InspectionError("CT15 run inspection scope mismatch")
    staged = session.scalar(
        select(PrivateStagedOutbox)
        .where(PrivateStagedOutbox.command_id == binding.command_id)
        .execution_options(populate_existing=True)
    )
    if staged is None or staged.payload is None:
        if row.state not in {"ABORTED", "PURGED"}:
            raise Ct15InspectionError("CT15 run inspection scope mismatch")
        return "erased_unverifiable"
    try:
        proposal = _parse_wire(
            staged.payload,
            StagedResultProposal,
            label="persisted CT15 staged command binding",
        )
    except W1WireContractError:
        raise Ct15InspectionError("CT15 run inspection scope mismatch") from None
    command = proposal.command
    if (
        staged.command_id != binding.command_id
        or command.command_id != binding.command_id
        or command.authenticated_owner_ref != binding.owner_ref
        or command.job_id != binding.job_id
        or command.source_id != source_id
    ):
        raise Ct15InspectionError("CT15 run inspection scope mismatch")
    return "matched"


def _scope_counts(
    session: Session, binding: Ct15InspectionBinding, row: PrivateCommitStage | None
) -> dict[str, object]:
    state_counts = {state: int(row is not None and row.state == state) for state in _STATES}
    staged_count = staged_payloads = pending_staged = ack_count = pending_ack = 0
    if row is not None:
        staged = session.scalar(
            select(PrivateStagedOutbox)
            .where(PrivateStagedOutbox.command_id == binding.command_id)
            .execution_options(populate_existing=True)
        )
        if staged is not None:
            staged_count = 1
            staged_payloads = int(staged.payload is not None)
            pending_staged = int(staged.payload is not None and staged.delivered_at is None)
        ack_count = int(
            session.scalar(
                select(func.count())
                .select_from(PrivateCommitGateAck)
                .where(PrivateCommitGateAck.command_id == binding.command_id)
            )
            or 0
        )
        pending_ack = int(
            session.scalar(
                select(func.count())
                .select_from(PrivateCommitGateAck)
                .where(
                    PrivateCommitGateAck.command_id == binding.command_id,
                    PrivateCommitGateAck.delivered_at.is_(None),
                )
            )
            or 0
        )
    return {
        "state_counts": state_counts,
        "result_payload_count": int(row is not None and row.result_payload is not None),
        "visible_result_count": int(row is not None and row.state == "FINALIZED"),
        "staged_outbox_count": staged_count,
        "staged_payload_count": staged_payloads,
        "pending_staged_count": pending_staged,
        "ack_count": ack_count,
        "pending_ack_count": pending_ack,
    }


def _total_counts(primary: dict[str, object], secondary: dict[str, object]) -> dict[str, object]:
    primary_states = primary["state_counts"]
    secondary_states = secondary["state_counts"]
    assert isinstance(primary_states, dict) and isinstance(secondary_states, dict)
    return {
        "state_counts": {
            state: int(primary_states[state]) + int(secondary_states[state]) for state in _STATES
        },
        **{key: cast(int, primary[key]) + cast(int, secondary[key]) for key in _COUNT_KEYS},
    }


def inspect_run_counts(session: Session, scope: Ct15RunInspectionScope) -> dict[str, object]:
    """Inspect exactly two validated scopes under one coherent command-lock snapshot."""

    try:
        if not isinstance(scope, Ct15RunInspectionScope):
            raise W1WireContractError("invalid CT15 run inspection scope")
        scope = _revalidate_model(
            scope,
            Ct15RunInspectionScope,
            label="CT15 run inspection scope",
        )
    except W1WireContractError:
        raise Ct15InspectionError("invalid CT15 run inspection scope") from None
    for command_id in sorted(
        (scope.primary.command_id, scope.secondary.command_id), key=lambda value: value.bytes
    ):
        _lock_command(session, command_id)

    rows = {
        "primary": session.get(
            PrivateCommitStage, scope.primary.command_id, populate_existing=True
        ),
        "secondary": session.get(
            PrivateCommitStage, scope.secondary.command_id, populate_existing=True
        ),
    }
    source_verification = {
        "primary": _stored_source_verification(
            session, scope.primary, scope.source_id, rows["primary"]
        ),
        "secondary": _stored_source_verification(
            session, scope.secondary, scope.source_id, rows["secondary"]
        ),
    }
    primary = _scope_counts(session, scope.primary, rows["primary"])
    secondary = _scope_counts(session, scope.secondary, rows["secondary"])
    return {
        "schema_version": "w2.ct15.run-inspection.v1",
        "run_id": scope.run_id,
        "counts": {
            "primary": primary,
            "secondary": secondary,
            "total": _total_counts(primary, secondary),
        },
        "source_verification": source_verification,
    }
