from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.api.errors import AuthenticationRequiredError
from app.core.config import settings
from app.db.session import SessionLocal, set_local_owner_context
from app.repo.identity import IdentityRepository
from app.services.auth_sessions import AuthSessionService


@dataclass(frozen=True)
class CurrentPrincipal:
    """Verified EPICK session mapped to the internal owner UUID."""

    issuer: str
    subject: str
    owner_user_id: UUID


def get_current_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentPrincipal:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthenticationRequiredError()
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise AuthenticationRequiredError()
    session = SessionLocal()
    try:
        with session.begin():
            verified = build_auth_session_service(IdentityRepository(session)).verify_access_token(
                token
            )
            return CurrentPrincipal(
                issuer=verified.issuer,
                subject=verified.subject,
                owner_user_id=verified.owner_user_id,
            )
    finally:
        session.close()


def build_auth_session_service(repository: IdentityRepository) -> AuthSessionService:
    signing_key = settings.epick_auth_signing_key
    refresh_pepper = settings.epick_refresh_token_pepper
    if signing_key is None or refresh_pepper is None:
        raise AuthenticationRequiredError()
    return AuthSessionService(
        repository,
        signing_key=signing_key.get_secret_value(),
        refresh_pepper=refresh_pepper.get_secret_value(),
        issuer=settings.epick_auth_issuer,
        audience=settings.epick_auth_audience,
        access_ttl_seconds=settings.epick_access_token_ttl_seconds,
        refresh_ttl_seconds=settings.epick_refresh_token_ttl_seconds,
    )


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
