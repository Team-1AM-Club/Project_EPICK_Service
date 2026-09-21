from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

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


@pytest.fixture
def fresh_migration_config(alembic_config: Config) -> Iterator[Config]:
    """Give historical upgrade tests a blank DB without downgrading live policy revisions."""

    del alembic_config
    admin_url, database_name = _admin_database_url(settings.test_database_url)
    fresh_name = f"{database_name[:32]}_migration_{uuid4().hex[:8]}"
    parsed = urlsplit(settings.test_database_url)
    fresh_url = urlunsplit((parsed.scheme, parsed.netloc, f"/{fresh_name}", "", ""))
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{fresh_name}"'))
        role_engine = create_engine(fresh_url)
        try:
            with role_engine.begin() as connection:
                connection.execute(text(RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")))
        finally:
            role_engine.dispose()
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", fresh_url)
        yield config
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{fresh_name}" WITH (FORCE)'))
        admin_engine.dispose()


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
