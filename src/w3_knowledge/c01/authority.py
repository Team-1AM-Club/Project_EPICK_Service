"""Fail-closed port for the authoritative W2 Source registry."""

from typing import Protocol
from uuid import UUID


class SourceAuthority(Protocol):
    def is_registered(self, source_id: UUID) -> bool:
        """Return literal True only for an authoritative registered Source."""


class SourceAuthorityUnavailable(RuntimeError):
    pass


class SourceNotRegistered(ValueError):
    pass


def require_registered(authority: SourceAuthority, source_id: str) -> None:
    identifier = UUID(source_id)
    try:
        registered = authority.is_registered(identifier)
    except Exception:
        raise SourceAuthorityUnavailable("SOURCE_AUTHORITY_UNAVAILABLE") from None
    if registered is not True:
        raise SourceNotRegistered("SOURCE_NOT_REGISTERED")
