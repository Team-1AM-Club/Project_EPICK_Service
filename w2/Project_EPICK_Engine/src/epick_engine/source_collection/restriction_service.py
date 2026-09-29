"""Internal authority-bound restriction mutation service."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.contracts import (
    AccuracyStatus,
    RestrictionStatus,
    SourceRestrictionSnapshot,
)
from epick_engine.source_collection.persistence import (
    PersistenceConflict,
    RestrictionMutationReceipt,
    SourceRestrictionIdentity,
    get_source_restriction_revision,
    lock_source_restriction_revision,
    record_source_restriction,
)

_CANONICAL_PROFILE = "w2.restriction.mutation.v1"
_LOCK_NAMESPACE = "epick.w2.restriction-mutation.v1"
_DENIED_MESSAGE = "restriction mutation denied"
_CONFLICT_MESSAGE = "restriction mutation conflict"


def _has_control_character(value: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in value)


class RestrictionMutation(BaseModel):
    """One validated internal request; authority identity is supplied out of band."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID
    kind: Literal["create", "clear", "reactivate"]
    source_id: UUID
    source_version_id: UUID | None
    restriction_id: UUID | None
    accuracy_status: AccuracyStatus
    reason_code: str
    evidence_refs: tuple[str, ...]
    changed_at: AwareDatetime
    replacement_ref: UUID | None

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        if not value or value.isspace() or _has_control_character(value):
            raise ValueError("reason_code must be non-blank and contain no control characters")
        return value

    @model_validator(mode="after")
    def validate_restriction_identity(self) -> RestrictionMutation:
        if self.kind == "create" and self.restriction_id is not None:
            raise ValueError("create must not specify a restriction_id")
        if self.kind != "create" and self.restriction_id is None:
            raise ValueError("clear and reactivate require a restriction_id")
        return self


class RestrictionMutationDenied(RuntimeError):
    """Raised with a fixed diagnostic when trusted caller authority is absent."""

    def __init__(self) -> None:
        super().__init__(_DENIED_MESSAGE)


class RestrictionMutationConflict(RuntimeError):
    """Raised with a fixed diagnostic for a rejected or inconsistent mutation."""

    def __init__(self) -> None:
        super().__init__(_CONFLICT_MESSAGE)


class RestrictionAuthorityPort(Protocol):
    """Trusted caller-context boundary required by every service instance."""

    def authorize(self, *, request: RestrictionMutation) -> str:
        """Authorize the request and return its canonical namespaced subject."""
        ...


def _canonical_request_bytes(request: RestrictionMutation) -> bytes:
    payload: dict[str, object] = {
        "profile": _CANONICAL_PROFILE,
        "kind": request.kind,
        "source_id": str(request.source_id),
        "source_version_id": (
            str(request.source_version_id) if request.source_version_id is not None else None
        ),
        "restriction_id": (
            str(request.restriction_id) if request.restriction_id is not None else None
        ),
        "accuracy_status": request.accuracy_status.value,
        "reason_code": request.reason_code,
        "evidence_refs": list(request.evidence_refs),
        "changed_at": request.changed_at.astimezone(UTC).isoformat(),
        "replacement_ref": (
            str(request.replacement_ref) if request.replacement_ref is not None else None
        ),
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_request_hash(request: RestrictionMutation) -> str:
    return hashlib.sha256(_canonical_request_bytes(request)).hexdigest()


def _request_lock_key(authority_ref: str, request_id: UUID) -> int:
    lock_bytes = json.dumps(
        [_LOCK_NAMESPACE, authority_ref, str(request_id)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(lock_bytes).digest()[:8], "big", signed=True)


def _valid_authority_ref(value: object) -> bool:
    if not isinstance(value, str) or not value or value.isspace():
        return False
    if _has_control_character(value):
        return False
    namespace, separator, subject = value.partition(":")
    return bool(
        separator and namespace and subject and not namespace.isspace() and not subject.isspace()
    )


class SourceRestrictionService:
    """Apply one authority-bound restriction change in one root transaction."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        authority: RestrictionAuthorityPort,
    ) -> None:
        self._session_factory = session_factory
        self._authority = authority

    def apply(self, request: RestrictionMutation) -> SourceRestrictionSnapshot:
        authority_ref = self._authorize(request)
        request_hash = _canonical_request_hash(request)
        try:
            with self._session_factory.begin() as session:
                return self._apply_in_transaction(
                    session,
                    request=request,
                    authority_ref=authority_ref,
                    request_hash=request_hash,
                )
        except RestrictionMutationConflict:
            raise
        except (PersistenceConflict, SQLAlchemyError):
            raise RestrictionMutationConflict() from None

    def _authorize(self, request: RestrictionMutation) -> str:
        try:
            authority_ref = self._authority.authorize(request=request)
        except RestrictionMutationDenied:
            raise RestrictionMutationDenied() from None
        except Exception:
            raise RestrictionMutationDenied() from None
        if not _valid_authority_ref(authority_ref):
            raise RestrictionMutationDenied()
        return authority_ref

    def _apply_in_transaction(
        self,
        session: Session,
        *,
        request: RestrictionMutation,
        authority_ref: str,
        request_hash: str,
    ) -> SourceRestrictionSnapshot:
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _request_lock_key(authority_ref, request.request_id)},
        )
        receipt = session.get(
            RestrictionMutationReceipt,
            (authority_ref, request.request_id),
        )
        if receipt is not None:
            if receipt.request_hash != request_hash:
                raise RestrictionMutationConflict()
            original = get_source_restriction_revision(
                session,
                restriction_id=receipt.restriction_id,
                restriction_revision=receipt.restriction_revision,
            )
            if original is None:
                raise RestrictionMutationConflict()
            return original

        restriction_revision = lock_source_restriction_revision(
            session,
            source_id=request.source_id,
        )
        restriction_id = self._restriction_id_for_new_revision(session, request)
        restriction_status = (
            RestrictionStatus.CLEARED if request.kind == "clear" else RestrictionStatus.ACTIVE
        )
        stored = record_source_restriction(
            session,
            snapshot=SourceRestrictionSnapshot(
                restriction_id=restriction_id,
                source_id=request.source_id,
                source_version_id=request.source_version_id,
                restriction_revision=restriction_revision,
                restriction_status=restriction_status,
                accuracy_status=request.accuracy_status,
                reason_code=request.reason_code,
                changed_at=request.changed_at,
                replacement_ref=request.replacement_ref,
            ),
            evidence_refs=request.evidence_refs,
        )
        session.add(
            RestrictionMutationReceipt(
                authority_ref=authority_ref,
                request_id=request.request_id,
                request_hash=request_hash,
                restriction_id=stored.restriction_id,
                restriction_revision=stored.restriction_revision,
                created_at=datetime.now(UTC),
            )
        )
        session.flush()
        return stored

    @staticmethod
    def _restriction_id_for_new_revision(
        session: Session,
        request: RestrictionMutation,
    ) -> UUID:
        if request.kind == "create":
            return uuid4()
        if request.restriction_id is None:
            raise RestrictionMutationConflict()
        identity = session.get(SourceRestrictionIdentity, request.restriction_id)
        if identity is None:
            raise RestrictionMutationConflict()
        if (
            identity.source_id != request.source_id
            or identity.source_version_id != request.source_version_id
        ):
            raise RestrictionMutationConflict()
        return request.restriction_id
