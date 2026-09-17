from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy

from app.core.config import settings
from app.runtime.lookup_adapter import create_configured_lookup_app

BACKEND_ROOT = Path(__file__).parents[2]
COMPOSE_PATH = BACKEND_ROOT / "infra" / "w1-runtime.compose.yml"


def test_configured_lookup_requires_its_own_database_url_even_when_worker_url_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "worker_database_url", "postgresql+psycopg://worker@example/worker")
    monkeypatch.setattr(settings, "lookup_database_url", None)
    monkeypatch.setattr(settings, "w1_w2_lookup_bearer", "lookup-test-token")

    with pytest.raises(RuntimeError, match="LOOKUP_DATABASE_URL"):
        create_configured_lookup_app()


def test_configured_lookup_uses_lookup_url_not_worker_url(monkeypatch: pytest.MonkeyPatch) -> None:
    configured_urls: list[str] = []

    def fake_create_engine(url: str, **kwargs: object) -> object:
        del kwargs
        configured_urls.append(url)
        return object()

    monkeypatch.setattr(settings, "worker_database_url", "postgresql+psycopg://worker@example/worker")
    monkeypatch.setattr(settings, "lookup_database_url", "postgresql+psycopg://lookup@example/lookup")
    monkeypatch.setattr(settings, "w1_w2_lookup_bearer", "lookup-test-token")
    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)

    app = create_configured_lookup_app()

    assert configured_urls == ["postgresql+psycopg://lookup@example/lookup"]
    assert any(route.path == "/internal/v1/job-commands/lookup" for route in app.routes)


def test_runtime_compose_keeps_lookup_in_a_separate_loopback_only_profile() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "w1-protected-lookup:" in compose
    assert "${W1_LOOKUP_ENV_FILE:?set W1_LOOKUP_ENV_FILE to the lookup-only env file}" in compose
    assert '"127.0.0.1:8080:8080"' in compose
    assert 'profiles: ["w1-lookup"]' in compose
    assert "W1_RUNTIME_ENV_FILE" not in compose.split("w1-protected-lookup:", 1)[1].split(
        "w1-commit-gate-recovery:", 1
    )[0]
