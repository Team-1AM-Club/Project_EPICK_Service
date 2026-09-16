from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app.api import dependencies
from app.api.dependencies import OwnerSessionDep
from tests.api.conftest import override_principal


@pytest.fixture
def rls_api_client(
    api_app: FastAPI,
    api_migrated_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    owner_one_id: UUID,
    owner_two_id: UUID,
) -> Iterator[tuple[TestClient, dict[str, UUID], Engine]]:
    """Exercise the actual request dependency against the migrated RLS database."""

    test_session_factory = sessionmaker(bind=api_migrated_engine, expire_on_commit=False)
    monkeypatch.setattr(dependencies, "SessionLocal", test_session_factory)
    with api_migrated_engine.begin() as connection:
        for owner_id, display_name in (
            (owner_one_id, "API owner one"),
            (owner_two_id, "API owner two"),
        ):
            connection.execute(
                text(
                    "INSERT INTO users (id, display_name, locale, timezone) "
                    "VALUES (:id, :display_name, 'ko-KR', 'Asia/Seoul')"
                ),
                {"id": owner_id, "display_name": display_name},
            )

    @api_app.get("/api/v1/_test/rls-context")
    def rls_context_probe(session: OwnerSessionDep) -> dict[str, str | None]:
        return {
            "owner_user_id": session.scalar(
                text("SELECT current_setting('app.current_user_id', true)")
            )
        }

    with TestClient(api_app) as client:
        yield client, {"one": owner_one_id, "two": owner_two_id}, api_migrated_engine


@pytest.mark.postgres
def test_owner_session_sets_request_local_rls_context_and_does_not_leak(
    api_app: FastAPI,
    rls_api_client: tuple[TestClient, dict[str, UUID], Engine],
) -> None:
    client, owners, engine = rls_api_client

    override_principal(api_app, owners["one"], subject="owner-one")
    first = client.get("/api/v1/_test/rls-context")
    override_principal(api_app, owners["two"], subject="owner-two")
    second = client.get("/api/v1/_test/rls-context")

    assert first.status_code == 200
    assert first.json() == {"owner_user_id": str(owners["one"])}
    assert second.status_code == 200
    assert second.json() == {"owner_user_id": str(owners["two"])}

    with engine.connect() as connection:
        leaked_owner = connection.scalar(
            text("SELECT current_setting('app.current_user_id', true)")
        )
    assert leaked_owner in {None, ""}
