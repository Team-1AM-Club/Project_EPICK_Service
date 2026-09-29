from __future__ import annotations

import pytest
from sqlalchemy.engine import URL

from epick_engine.source_collection.persistence import (
    PersistenceConfigurationError,
    create_database_engine,
    create_session_factory,
    database_url_from_environment,
    session_scope,
)


def test_database_url_requires_an_explicit_environment_value(monkeypatch) -> None:
    monkeypatch.delenv("EPICK_DATABASE_URL", raising=False)

    with pytest.raises(PersistenceConfigurationError, match="EPICK_DATABASE_URL"):
        database_url_from_environment()


def test_database_url_accepts_postgresql_without_exposing_credentials(monkeypatch) -> None:
    secret = "not-for-logs"
    monkeypatch.setenv(
        "EPICK_DATABASE_URL",
        f"postgresql+psycopg://epick:{secret}@db.example.test/epick",
    )

    url = database_url_from_environment()

    assert isinstance(url, URL)
    assert url.drivername == "postgresql+psycopg"
    assert secret not in url.render_as_string(hide_password=True)


def test_non_postgresql_database_is_rejected_without_echoing_the_value(monkeypatch) -> None:
    secret = "sqlite:///private-path.db"
    monkeypatch.setenv("EPICK_DATABASE_URL", secret)

    with pytest.raises(PersistenceConfigurationError) as captured:
        database_url_from_environment()

    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://epick:secret@db.example.test/epick",
        "postgresql+psycopg2://epick:secret@db.example.test/epick",
        "postgresql+asyncpg://epick:secret@db.example.test/epick",
    ],
)
def test_database_url_rejects_non_psycopg3_drivers(monkeypatch, database_url: str) -> None:
    monkeypatch.setenv("EPICK_DATABASE_URL", database_url)

    with pytest.raises(PersistenceConfigurationError):
        database_url_from_environment()


def test_engine_and_session_factory_are_synchronous_and_session_scoped() -> None:
    url = URL.create(
        "postgresql+psycopg",
        username="epick",
        password="secret",
        host="db.example.test",
        database="epick",
    )
    engine = create_database_engine(url)
    factory = create_session_factory(engine)

    first = factory()
    second = factory()
    try:
        assert first is not second
        assert first.bind is engine
        assert second.bind is engine
    finally:
        first.close()
        second.close()
        engine.dispose()


def test_session_scope_always_closes_the_session() -> None:
    class FakeSession:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeSession()

    with session_scope(lambda: fake):
        assert fake.closed is False
    assert fake.closed is True
