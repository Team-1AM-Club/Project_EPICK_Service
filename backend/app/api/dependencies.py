from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.api.errors import AuthenticationRequiredError
from app.db.session import SessionLocal, set_local_owner_context


@dataclass(frozen=True)
class CurrentPrincipal:
    """Verified identity mapped to the internal owner UUID.

    API-0 deliberately has no production OIDC adapter. The default dependency
    therefore fails closed; tests override it after creating a matching
    synthetic User row in the isolated PostgreSQL database.
    """

    issuer: str
    subject: str
    owner_user_id: UUID


def get_current_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentPrincipal:
    """Fail closed until the deployment-specific OIDC adapter is configured."""

    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthenticationRequiredError()
    raise AuthenticationRequiredError()


CurrentPrincipalDep = Annotated[CurrentPrincipal, Depends(get_current_principal)]


def get_owner_session(principal: CurrentPrincipalDep) -> Iterator[Session]:
    """Open one request transaction and scope PostgreSQL RLS to its owner."""

    session = SessionLocal()
    try:
        with session.begin():
            set_local_owner_context(session, principal.owner_user_id)
            yield session
    finally:
        session.close()


OwnerSessionDep = Annotated[Session, Depends(get_owner_session)]
