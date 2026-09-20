from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.dependencies import build_auth_session_service
from app.api.errors import AuthenticationRequiredError, OriginNotAllowedError
from app.api.schemas.auth import (
    LOCAL_REFRESH_COOKIE,
    OIDC_TRANSACTION_COOKIE,
    SECURE_REFRESH_COOKIE,
    AccessTokenResponse,
)
from app.core.config import settings
from app.db.session import SessionLocal
from app.repo.identity import IdentityRepository
from app.services.auth_sessions import RefreshReplayDetected
from app.services.google_oidc import GoogleOidcService
from app.services.identity import IdentityService

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def get_auth_database_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


AuthDatabaseSessionDep = Annotated[Session, Depends(get_auth_database_session)]


def get_google_oidc_service() -> GoogleOidcService:
    if (
        not settings.google_client_id
        or settings.google_client_secret is None
        or settings.epick_auth_signing_key is None
    ):
        raise AuthenticationRequiredError()
    return GoogleOidcService(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        redirect_uri=settings.google_oidc_redirect_uri,
        authorization_endpoint=settings.google_oidc_authorization_endpoint,
        discovery_url=settings.google_oidc_discovery_url,
        transaction_signing_key=settings.epick_auth_signing_key.get_secret_value(),
        transaction_ttl_seconds=settings.epick_oidc_transaction_ttl_seconds,
    )


GoogleOidcServiceDep = Annotated[GoogleOidcService, Depends(get_google_oidc_service)]


@router.get("/google/start", status_code=302, response_class=RedirectResponse)
def start_google_login(
    oidc: GoogleOidcServiceDep,
    return_to: Annotated[str, Query(max_length=512)] = "/",
) -> RedirectResponse:
    authorization = oidc.create_authorization_request(return_to)
    response = RedirectResponse(authorization.authorization_url, status_code=302)
    response.set_cookie(
        OIDC_TRANSACTION_COOKIE,
        authorization.cookie_value,
        max_age=settings.epick_oidc_transaction_ttl_seconds,
        httponly=True,
        secure=settings.epick_auth_cookie_secure,
        samesite="lax",
        path="/api/v1/auth/google",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/google/callback", status_code=302, response_class=RedirectResponse)
def complete_google_login(
    request: Request,
    session: AuthDatabaseSessionDep,
    oidc: GoogleOidcServiceDep,
    code: Annotated[str, Query(min_length=1, max_length=4096)],
    state: Annotated[str, Query(min_length=16, max_length=512)],
) -> RedirectResponse:
    transaction_cookie = request.cookies.get(OIDC_TRANSACTION_COOKIE)
    if not transaction_cookie:
        logger.warning("google_oidc_callback_rejected stage=transaction_cookie reason=missing")
        raise AuthenticationRequiredError()
    try:
        transaction = oidc.consume_transaction(transaction_cookie, state)
    except AuthenticationRequiredError:
        logger.warning("google_oidc_callback_rejected stage=transaction_cookie reason=invalid")
        raise
    claims = oidc.exchange_code(code=code, transaction=transaction)
    repository = IdentityRepository(session)
    identity = IdentityService(repository).get_or_create_google_identity(
        provider_subject=claims.subject,
        display_name=claims.display_name,
        provider_email=claims.email,
        provider_email_verified=claims.email_verified,
        locale=claims.locale,
        timezone="Asia/Seoul",
        login_at=oidc.now(),
    )
    issued = build_auth_session_service(repository).issue(
        identity,
        created_ip_hash=_bounded_hash(request.client.host if request.client else None),
        user_agent_summary=request.headers.get("user-agent"),
    )
    session.commit()
    destination = f"{settings.epick_frontend_url.rstrip('/')}{transaction.return_to}"
    response = RedirectResponse(destination, status_code=302)
    response.delete_cookie(OIDC_TRANSACTION_COOKIE, path="/api/v1/auth/google")
    _set_refresh_cookie(response, issued.refresh_token)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh_epick_access_token(
    request: Request,
    response: Response,
    session: AuthDatabaseSessionDep,
) -> AccessTokenResponse:
    _require_allowed_browser_origin(request)
    raw_refresh = _read_refresh_cookie(request)
    if not raw_refresh:
        raise AuthenticationRequiredError()
    try:
        issued = build_auth_session_service(IdentityRepository(session)).rotate(raw_refresh)
    except RefreshReplayDetected:
        session.commit()
        raise AuthenticationRequiredError() from None
    session.commit()
    _set_refresh_cookie(response, issued.refresh_token)
    response.headers["Cache-Control"] = "no-store"
    return AccessTokenResponse(
        access_token=issued.access_token,
        expires_in=issued.access_expires_in,
    )


@router.post("/logout", status_code=204)
def logout_epick_session(
    request: Request,
    response: Response,
    session: AuthDatabaseSessionDep,
) -> None:
    _require_allowed_browser_origin(request)
    build_auth_session_service(IdentityRepository(session)).logout(_read_refresh_cookie(request))
    session.commit()
    _clear_refresh_cookies(response)
    response.headers["Cache-Control"] = "no-store"


def _refresh_cookie_name() -> str:
    return SECURE_REFRESH_COOKIE if settings.epick_auth_cookie_secure else LOCAL_REFRESH_COOKIE


def _set_refresh_cookie(response: Response, value: str) -> None:
    response.set_cookie(
        _refresh_cookie_name(),
        value,
        max_age=settings.epick_refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.epick_auth_cookie_secure,
        samesite="lax",
        path="/" if settings.epick_auth_cookie_secure else "/api/v1/auth",
    )


def _clear_refresh_cookies(response: Response) -> None:
    response.delete_cookie(LOCAL_REFRESH_COOKIE, path="/api/v1/auth")
    response.delete_cookie(SECURE_REFRESH_COOKIE, path="/")


def _read_refresh_cookie(request: Request) -> str | None:
    return request.cookies.get(_refresh_cookie_name())


def _require_allowed_browser_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer:
            parsed = urlsplit(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in settings.epick_allowed_frontend_origins:
        raise OriginNotAllowedError()


def _bounded_hash(value: str | None) -> str | None:
    return hashlib.sha256(value.encode()).hexdigest() if value else None
