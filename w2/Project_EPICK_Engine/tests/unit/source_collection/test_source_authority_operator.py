"""W1 deployment composition for the W2 internal Source authority endpoint."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from fastapi import FastAPI

from epick_engine.source_collection import source_authority_operator


def test_factory_requires_injected_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EPICK_W2_SOURCE_AUTHORITY_TOKEN", raising=False)
    engine = Mock()
    monkeypatch.setattr(source_authority_operator, "create_database_engine", lambda _: engine)

    with pytest.raises(ValueError, match="service token is required"):
        source_authority_operator.create_app()

    engine.dispose.assert_not_called()


def test_factory_connects_authority_api_to_w2_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EPICK_W2_SOURCE_AUTHORITY_TOKEN", "private-service-token")
    engine = Mock()
    session_factory = Mock()
    app = FastAPI()
    seen: dict[str, object] = {}
    monkeypatch.setattr(source_authority_operator, "database_url_from_environment", lambda: "db")
    monkeypatch.setattr(source_authority_operator, "create_database_engine", lambda _: engine)
    monkeypatch.setattr(
        source_authority_operator, "create_session_factory", lambda _: session_factory
    )

    def fake_api(*, session_factory: object, bearer_token: str) -> FastAPI:
        seen.update(session_factory=session_factory, bearer_token=bearer_token)
        return app

    monkeypatch.setattr(source_authority_operator, "create_source_authority_api", fake_api)

    assert source_authority_operator.create_app() is app
    assert seen == {"session_factory": session_factory, "bearer_token": "private-service-token"}
    assert app.state.w2_engine is engine
