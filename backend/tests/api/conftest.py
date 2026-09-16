from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine, create_engine, text

from app.api.dependencies import CurrentPrincipal, CurrentPrincipalDep, get_current_principal
from app.core.config import settings
from app.main import create_app


class _ValidationPayload(BaseModel):
    name: str


_DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _admin_database_url(database_url: str) -> tuple[str, str]:
    parsed = urlsplit(database_url)
    database_name = parsed.path.removeprefix("/")
    if not _DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise ValueError("test database name must be a simple PostgreSQL identifier")
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", "", "")), database_name


@pytest.fixture(scope="session")
def api_migrated_engine() -> Iterator[Engine]:
    """Keep API integration tests self-contained while using the normal Alembic head."""

    backend_root = Path(__file__).parents[2]
    admin_url, database_name = _admin_database_url(settings.test_database_url)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": database_name},
            ).scalar()
            if exists is None:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.test_database_url)
    command.upgrade(config, "head")
    engine = create_engine(settings.test_database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def owner_one_id() -> UUID:
    return uuid4()


@pytest.fixture
def owner_two_id() -> UUID:
    return uuid4()


@pytest.fixture
def api_app() -> FastAPI:
    """Add test-only probes to an isolated factory app, never to production routes."""

    app = create_app()

    @app.get("/api/v1/_test/principal")
    def principal_probe(principal: CurrentPrincipalDep) -> dict[str, str]:
        return {"owner_user_id": str(principal.owner_user_id)}

    @app.post("/api/v1/_test/validation")
    def validation_probe(payload: _ValidationPayload) -> dict[str, str]:
        return {"name": payload.name}

    return app


@pytest.fixture
def api_client(api_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(api_app) as client:
        yield client


def override_principal(app: FastAPI, owner_user_id: UUID, *, subject: str = "test-subject") -> None:
    app.dependency_overrides[get_current_principal] = lambda: CurrentPrincipal(
        issuer="https://issuer.test",
        subject=subject,
        owner_user_id=owner_user_id,
    )
