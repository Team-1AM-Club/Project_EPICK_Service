from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

BACKEND_ROOT = Path(__file__).parents[3]
DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RUNTIME_ROLE_TEMPLATE_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_roles.sql"


def _admin_database_url(database_url: str) -> tuple[str, str]:
    parsed = urlsplit(database_url)
    database_name = parsed.path.removeprefix("/")
    if not DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise ValueError("test database name must be a simple PostgreSQL identifier")
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", "", "")), database_name


@pytest.fixture(scope="session")
def alembic_config() -> Config:
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

    role_engine = create_engine(settings.test_database_url)
    try:
        with role_engine.begin() as connection:
            connection.execute(text(RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")))
    finally:
        role_engine.dispose()

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.test_database_url)
    return config


@pytest.fixture(scope="session")
def migrated_engine(alembic_config: Config) -> Iterator[Engine]:
    command.upgrade(alembic_config, "head")
    engine = create_engine(settings.test_database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    session = sessionmaker(bind=migrated_engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
