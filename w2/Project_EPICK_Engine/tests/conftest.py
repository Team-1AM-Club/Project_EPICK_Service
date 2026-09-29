"""Shared test boundaries for W2 source collection.

Synthetic transports never relax the production URL policy. Tests that need a
real PostgreSQL connection must opt in with both a marker and an explicit
environment approval.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest
from sqlalchemy.engine import URL, make_url

SYNTHETIC_HOST_SUFFIX = ".test"
TEST_DATABASE_APPROVAL_ENV = "EPICK_TEST_DATABASE_APPROVED"
TEST_DATABASE_URL_ENV = "EPICK_TEST_DATABASE_URL"


@dataclass(frozen=True, slots=True)
class SyntheticPrincipal:
    """Minimal authenticated owner reference supplied by a W1 test adapter."""

    owner_ref: UUID


@dataclass(slots=True)
class FakeCollectionDispatch:
    """Recording fake for the W1-to-W2 dispatch port, not a W1 scheduler."""

    submitted: list[Mapping[str, Any]] = field(default_factory=list)

    def submit(self, command: Mapping[str, Any]) -> None:
        self.submitted.append(dict(command))


def _deny_network(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError(
        "External network is blocked in tests; use synthetic_transport or an approved marker."
    )


@pytest.fixture(autouse=True)
def block_external_network(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Deny socket connections unless the test explicitly needs approved PostgreSQL."""

    if request.node.get_closest_marker("approved_postgres") is not None:
        return
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket.socket, "connect", _deny_network)


@pytest.fixture
def synthetic_transport() -> Callable[[Mapping[str, httpx.Response]], httpx.MockTransport]:
    """Build an in-memory transport limited to clearly synthetic ``.test`` URLs."""

    def build(responses: Mapping[str, httpx.Response]) -> httpx.MockTransport:
        normalized = dict(responses)
        for url in normalized:
            host = urlsplit(url).hostname or ""
            if not host.endswith(SYNTHETIC_HOST_SUFFIX):
                raise ValueError("Synthetic transport URLs must use a .test hostname")

        def handle(request: httpx.Request) -> httpx.Response:
            response = normalized.get(str(request.url))
            if response is None:
                raise AssertionError(
                    f"Unexpected synthetic request: {request.method} {request.url}"
                )
            response.headers["X-EPICK-Synthetic"] = "true"
            return response

        return httpx.MockTransport(handle)

    return build


@pytest.fixture
def test_principal() -> SyntheticPrincipal:
    return SyntheticPrincipal(owner_ref=UUID("00000000-0000-4000-8000-000000000101"))


@pytest.fixture
def fake_dispatch() -> FakeCollectionDispatch:
    return FakeCollectionDispatch()


@pytest.fixture
def approved_postgres_url(request: pytest.FixtureRequest) -> URL:
    """Return a redaction-safe parsed DSN only after explicit test DB approval."""

    if request.node.get_closest_marker("approved_postgres") is None:
        pytest.fail("approved_postgres_url requires the approved_postgres marker")

    database_url = os.environ.get(TEST_DATABASE_URL_ENV)
    approved = os.environ.get(TEST_DATABASE_APPROVAL_ENV) == "1"
    if not database_url or not approved:
        pytest.skip(
            f"Set {TEST_DATABASE_URL_ENV} and {TEST_DATABASE_APPROVAL_ENV}=1 "
            "for an isolated approved test database"
        )

    production_url = os.environ.get("EPICK_DATABASE_URL")
    if production_url and database_url == production_url:
        pytest.fail("The approved test database must not equal EPICK_DATABASE_URL")

    parsed = make_url(database_url)
    if parsed.drivername.split("+", maxsplit=1)[0] not in {"postgres", "postgresql"}:
        pytest.fail("EPICK_TEST_DATABASE_URL must use PostgreSQL")
    if not parsed.database:
        pytest.fail("EPICK_TEST_DATABASE_URL must name an isolated database")
    return parsed
