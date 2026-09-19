from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy

from app.core.config import settings
from app.runtime.w4_question_core_context_adapter import (
    create_configured_w4_question_core_context_app,
)

BACKEND_ROOT = Path(__file__).parents[2]
COMPOSE_PATH = BACKEND_ROOT / "infra" / "w1-runtime.compose.yml"


def test_w4_context_requires_its_own_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "worker_database_url", "postgresql+psycopg://worker@example/worker")
    monkeypatch.setattr(settings, "w4_context_database_url", None)
    monkeypatch.setattr(settings, "w1_w4_context_bearer", "w4-context-test-token")

    with pytest.raises(RuntimeError, match="W4_CONTEXT_DATABASE_URL"):
        create_configured_w4_question_core_context_app()


def test_w4_context_uses_its_own_url_not_the_worker_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_urls: list[str] = []

    def fake_create_engine(url: str, **kwargs: object) -> object:
        del kwargs
        configured_urls.append(url)
        return object()

    monkeypatch.setattr(settings, "worker_database_url", "postgresql+psycopg://worker@example/worker")
    monkeypatch.setattr(
        settings,
        "w4_context_database_url",
        "postgresql+psycopg://w4-context@example/w4-context",
    )
    monkeypatch.setattr(settings, "w1_w4_context_bearer", "w4-context-test-token")
    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)

    app = create_configured_w4_question_core_context_app()

    assert configured_urls == ["postgresql+psycopg://w4-context@example/w4-context"]
    assert any(
        route.path == "/internal/v1/w4/question-core-contexts/resolve" for route in app.routes
    )


def test_runtime_compose_keeps_w4_context_internal_and_separate_from_w2_lookup() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    section = compose.split("w1-w4-question-core-context:", 1)[1]

    env_file_reference = (
        "${W1_W4_CONTEXT_ENV_FILE:?set W1_W4_CONTEXT_ENV_FILE to the W4 context-only env file}"
    )
    assert env_file_reference in section
    assert 'profiles: ["w4-question-core-context"]' in section
    assert 'expose:\n      - "8081"' in section
    assert "ports:" not in section
    assert "W1_LOOKUP_ENV_FILE" not in section
    assert "W1_RUNTIME_ENV_FILE" not in section
