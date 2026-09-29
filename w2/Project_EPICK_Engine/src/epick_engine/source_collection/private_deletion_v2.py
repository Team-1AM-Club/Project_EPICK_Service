"""Scope-owned W2 private deletion v2 command and acknowledgement contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol, Self, cast
from uuid import UUID

from sqlalchemy import select

from epick_engine.source_collection.worker import (
    WorkerContractViolation,
    _require_private_deletion_command_epoch,
    _require_private_deletion_keys,
    _require_private_deletion_uuid,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

type PrivateDeletionScopeKind = Literal["ACCOUNT", "PROJECT"]
type PrivateDeletionAckOutcomeV2 = Literal["APPLIED", "DUPLICATE"]

_COMMAND_SCHEMA_VERSION = "w2.private-deletion.v2"
_ACK_SCHEMA_VERSION = "w2.private-deletion-ack.v2"
_COMMAND_KEYS = frozenset(
    {"schema_version", "deletion_id", "owner_user_id", "deletion_epoch", "scope"}
)
_ACK_KEYS = frozenset(
    {"schema_version", "deletion_id", "owner_user_id", "deletion_epoch", "scope", "outcome"}
)
_ACCOUNT_SCOPE_KEYS = frozenset({"type"})
_PROJECT_SCOPE_KEYS = frozenset({"type", "project_id"})
_ACK_OUTCOMES = frozenset({"APPLIED", "DUPLICATE"})


@dataclass(frozen=True, slots=True)
class PrivateDeletionScope:
    """Explicit account-wide or owner-and-Project private deletion scope."""

    kind: PrivateDeletionScopeKind
    project_id: UUID | None

    def __post_init__(self) -> None:
        if self.kind == "ACCOUNT":
            if self.project_id is not None:
                raise WorkerContractViolation("ACCOUNT scope must not contain project_id")
            return
        if self.kind == "PROJECT":
            if not isinstance(self.project_id, UUID):
                raise WorkerContractViolation("PROJECT scope requires a UUID project_id")
            return
        raise WorkerContractViolation("private deletion scope type is invalid")

    @classmethod
    def from_mapping(cls, raw: object) -> Self:
        if not isinstance(raw, Mapping):
            raise WorkerContractViolation("private deletion scope must be an object")
        scope = cast(Mapping[str, object], raw)
        kind = scope.get("type")
        if kind == "ACCOUNT":
            _require_private_deletion_keys(
                scope,
                expected=_ACCOUNT_SCOPE_KEYS,
                payload_name="ACCOUNT private deletion scope",
            )
            return cls(kind="ACCOUNT", project_id=None)
        if kind == "PROJECT":
            _require_private_deletion_keys(
                scope,
                expected=_PROJECT_SCOPE_KEYS,
                payload_name="PROJECT private deletion scope",
            )
            return cls(
                kind="PROJECT",
                project_id=_require_private_deletion_uuid(scope, "project_id"),
            )
        raise WorkerContractViolation("private deletion scope type is invalid")

    def to_mapping(self) -> dict[str, object]:
        if self.kind == "ACCOUNT":
            return {"type": "ACCOUNT"}
        assert self.project_id is not None
        return {"type": "PROJECT", "project_id": str(self.project_id)}


@dataclass(frozen=True, slots=True)
class PrivateDeletionCommandV2:
    """Validated W2-owned private deletion command with an explicit scope."""

    deletion_id: UUID
    owner_user_id: UUID
    deletion_epoch: int
    scope: PrivateDeletionScope

    def __post_init__(self) -> None:
        if not isinstance(self.deletion_id, UUID):
            raise WorkerContractViolation("deletion_id must be a UUID")
        if not isinstance(self.owner_user_id, UUID):
            raise WorkerContractViolation("owner_user_id must be a UUID")
        _require_private_deletion_command_epoch({"deletion_epoch": self.deletion_epoch})
        if not isinstance(self.scope, PrivateDeletionScope):
            raise WorkerContractViolation("scope must be a PrivateDeletionScope")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> Self:
        _require_private_deletion_keys(
            raw,
            expected=_COMMAND_KEYS,
            payload_name="private deletion v2 command",
        )
        if raw["schema_version"] != _COMMAND_SCHEMA_VERSION:
            raise WorkerContractViolation("private deletion v2 schema version is invalid")
        return cls(
            deletion_id=_require_private_deletion_uuid(raw, "deletion_id"),
            owner_user_id=_require_private_deletion_uuid(raw, "owner_user_id"),
            deletion_epoch=_require_private_deletion_command_epoch(raw),
            scope=PrivateDeletionScope.from_mapping(raw["scope"]),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": _COMMAND_SCHEMA_VERSION,
            "deletion_id": str(self.deletion_id),
            "owner_user_id": str(self.owner_user_id),
            "deletion_epoch": self.deletion_epoch,
            "scope": self.scope.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class PrivateDeletionAckV2:
    """Validated completion ACK that echoes the accepted v2 deletion scope."""

    deletion_id: UUID
    owner_user_id: UUID
    deletion_epoch: int
    scope: PrivateDeletionScope
    outcome: PrivateDeletionAckOutcomeV2

    def __post_init__(self) -> None:
        if not isinstance(self.deletion_id, UUID):
            raise WorkerContractViolation("deletion_id must be a UUID")
        if not isinstance(self.owner_user_id, UUID):
            raise WorkerContractViolation("owner_user_id must be a UUID")
        _require_private_deletion_command_epoch({"deletion_epoch": self.deletion_epoch})
        if not isinstance(self.scope, PrivateDeletionScope):
            raise WorkerContractViolation("scope must be a PrivateDeletionScope")
        if not isinstance(self.outcome, str) or self.outcome not in _ACK_OUTCOMES:
            raise WorkerContractViolation("private deletion v2 ACK outcome is invalid")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> Self:
        _require_private_deletion_keys(
            raw,
            expected=_ACK_KEYS,
            payload_name="private deletion v2 acknowledgement",
        )
        if raw["schema_version"] != _ACK_SCHEMA_VERSION:
            raise WorkerContractViolation("private deletion v2 ACK schema version is invalid")
        outcome = raw["outcome"]
        if not isinstance(outcome, str) or outcome not in _ACK_OUTCOMES:
            raise WorkerContractViolation("private deletion v2 ACK outcome is invalid")
        return cls(
            deletion_id=_require_private_deletion_uuid(raw, "deletion_id"),
            owner_user_id=_require_private_deletion_uuid(raw, "owner_user_id"),
            deletion_epoch=_require_private_deletion_command_epoch(raw),
            scope=PrivateDeletionScope.from_mapping(raw["scope"]),
            outcome=cast(PrivateDeletionAckOutcomeV2, outcome),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": _ACK_SCHEMA_VERSION,
            "deletion_id": str(self.deletion_id),
            "owner_user_id": str(self.owner_user_id),
            "deletion_epoch": self.deletion_epoch,
            "scope": self.scope.to_mapping(),
            "outcome": self.outcome,
        }


def command_digest_v2(command: PrivateDeletionCommandV2) -> str:
    """Return the canonical SHA-256 digest for an exact v2 command body."""

    return hashlib.sha256(
        json.dumps(
            command.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


class PrivateDeletionAckCallbackV2(Protocol):
    """Single W1 callback that atomically purges its scope and records the ACK."""

    def acknowledge(self, *, acknowledgement: PrivateDeletionAckV2) -> None:
        """Send one exact v2 ACK after the W2 transaction commits."""


def process_private_deletion_v2(
    session_factory: sessionmaker[Session],
    command: PrivateDeletionCommandV2,
    callback: PrivateDeletionAckCallbackV2,
) -> PrivateDeletionAckV2 | None:
    """Commit W2 scope deletion before one W1 purge-and-ACK callback."""

    from epick_engine.source_collection.persistence import (  # noqa: PLC0415
        PrivateDeletionReceipt,
        apply_private_deletion_v2,
    )

    with session_factory.begin() as session:
        outcome = apply_private_deletion_v2(session, command)
    if outcome == "STALE":
        return None

    acknowledgement = PrivateDeletionAckV2(
        deletion_id=command.deletion_id,
        owner_user_id=command.owner_user_id,
        deletion_epoch=command.deletion_epoch,
        scope=command.scope,
        outcome=outcome,
    )
    callback.acknowledge(acknowledgement=acknowledgement)
    with session_factory.begin() as session:
        receipt = session.scalar(
            select(PrivateDeletionReceipt)
            .where(PrivateDeletionReceipt.deletion_id == command.deletion_id)
            .with_for_update()
        )
        if (
            receipt is None
            or receipt.contract_version != _COMMAND_SCHEMA_VERSION
            or receipt.command_digest != command_digest_v2(command)
        ):
            raise RuntimeError("private deletion ACK receipt binding is invalid")
        if receipt.ack_confirmed_at is None:
            receipt.ack_confirmed_at = datetime.now(UTC)
    return acknowledgement
