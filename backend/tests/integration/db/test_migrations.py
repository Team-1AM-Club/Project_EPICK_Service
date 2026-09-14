from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings

BACKEND_ROOT = Path(__file__).parents[3]
DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.test_database_url)
    return config


@pytest.mark.postgres
def test_blank_database_upgrades_downgrades_and_reupgrades(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


@pytest.mark.postgres
def test_existing_pre_completion_database_upgrades_forward_to_the_current_head(
    alembic_config: Config,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "002_create_experience_repository")
    command.upgrade(alembic_config, "head")

    engine = create_engine(settings.test_database_url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("users")}
    finally:
        engine.dispose()

    assert "deletion_epoch" in columns
