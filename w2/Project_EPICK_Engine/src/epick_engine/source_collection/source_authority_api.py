"""Internal, fail-closed W2 Source registry lookup for W3 C-01.

This boundary deliberately exposes no public-owner API behavior and no Source
metadata.  W3 may use a literal ``registered: true`` only as confirmation that
the requested Source ID exists in W2's authoritative PostgreSQL registry.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from epick_engine.source_collection.persistence import Source

_AUTHENTICATION_ERROR = {"code": "SOURCE_AUTHORITY_UNAUTHORIZED"}
_INVALID_SOURCE_ID_ERROR = {"code": "INVALID_SOURCE_ID"}
_UNAVAILABLE_ERROR = {"code": "SOURCE_AUTHORITY_UNAVAILABLE"}


class SourceAuthorityResponse(BaseModel):
    """The complete internal authority response projection."""

    model_config = ConfigDict(extra="forbid")

    source_id: UUID
    registered: bool


def create_source_authority_api(
    *,
    session_factory: Callable[[], Session],
    bearer_token: str,
) -> FastAPI:
    """Create the W3-only authority lookup application.

    ``bearer_token`` is injected by deployment composition and is intentionally
    neither loaded from an ambient environment nor included in errors or logs.
    ``session_factory`` belongs to the W2 persistence composition; lookup errors
    remain unavailable instead of being projected as an unregistered Source.
    """

    if not isinstance(bearer_token, str) or not bearer_token:
        raise ValueError("bearer_token must not be empty")

    app = FastAPI(
        title="EPick W2 Source Authority",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get(
        "/internal/v1/sources/{raw_source_id}/authority",
        response_model=SourceAuthorityResponse,
        response_model_exclude_none=True,
    )
    def get_source_authority(
        raw_source_id: str,
        authorization: Annotated[str | None, Header()] = None,
    ) -> SourceAuthorityResponse:
        _require_bearer(authorization, bearer_token)
        source_id = _parse_canonical_source_id(raw_source_id)

        try:
            with session_factory() as session:
                registered = session.get(Source, source_id) is not None
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_UNAVAILABLE_ERROR,
            ) from exc

        return SourceAuthorityResponse(source_id=source_id, registered=registered)

    return app


def _require_bearer(authorization: str | None, bearer_token: str) -> None:
    if authorization is None:
        _raise_unauthorized()

    scheme, separator, candidate_token = authorization.partition(" ")
    if (
        scheme != "Bearer"
        or separator != " "
        or not candidate_token
        or not secrets.compare_digest(candidate_token, bearer_token)
    ):
        _raise_unauthorized()


def _raise_unauthorized() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_AUTHENTICATION_ERROR,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _parse_canonical_source_id(raw_source_id: str) -> UUID:
    try:
        source_id = UUID(raw_source_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_INVALID_SOURCE_ID_ERROR,
        ) from exc

    if str(source_id) != raw_source_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_INVALID_SOURCE_ID_ERROR,
        )
    return source_id
