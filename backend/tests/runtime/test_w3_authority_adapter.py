from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.runtime.w3_authority_adapter import create_w3_authority_private_app
from app.services.w3_authority import W3AuthorityConflictError, W3AuthorityNotFoundError


class _Sessions:
    @contextmanager
    def begin(self):
        yield object()


def _client() -> TestClient:
    return TestClient(
        create_w3_authority_private_app(
            session_factory=_Sessions(),
            expected_bearer_token="w3-authority-secret",
            expected_principal="epick-w3-core-runtime",
        )
    )


def _headers(*, principal: str = "epick-w3-core-runtime") -> dict[str, str]:
    return {
        "Authorization": "Bearer w3-authority-secret",
        "X-EPICK-Service-Principal": principal,
    }


def _request(*, job_id: UUID | None = None, source_id: UUID | None = None) -> dict[str, str]:
    return {
        "schema_version": "w1.private.w3-authority-request.v1",
        "job_id": str(job_id or uuid4()),
        "source_id": str(source_id or uuid4()),
    }


def _authorization(*, job_id: UUID, source_id: UUID):
    return SimpleNamespace(
        context=SimpleNamespace(
            job_id=job_id,
            company_id=uuid4(),
            source_id=source_id,
            analysis_input_version="analysis:v7",
        ),
        owner_id=uuid4(),
        owner_epoch=2,
        active=True,
    )


def test_private_authority_requires_bearer_and_expected_principal() -> None:
    path = "/internal/v1/w3/authority/current"

    unauthenticated = _client().post(path, json=_request())
    forbidden = _client().post(path, headers=_headers(principal="other-worker"), json=_request())

    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {
        "schema_version": "w1.private.error.v1",
        "code": "W3_AUTHORITY_UNAUTHENTICATED",
        "retryable": False,
        "correlation_id": unauthenticated.json()["correlation_id"],
    }
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "W3_AUTHORITY_FORBIDDEN"
    assert "w3-authority-secret" not in unauthenticated.text + forbidden.text


def test_private_authority_returns_only_the_w3_authorization_projection() -> None:
    job_id, source_id = uuid4(), uuid4()
    authorization = _authorization(job_id=job_id, source_id=source_id)

    with patch(
        "app.runtime.w3_authority_adapter.W3AuthorityService.current",
        return_value=authorization,
    ):
        response = _client().post(
            "/internal/v1/w3/authority/current",
            headers=_headers(),
            json=_request(job_id=job_id, source_id=source_id),
        )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "w1.private.w3-authority-response.v1",
        "context": {
            "job_id": str(job_id),
            "company_id": str(authorization.context.company_id),
            "source_id": str(source_id),
            "analysis_input_version": "analysis:v7",
        },
        "owner_id": str(authorization.owner_id),
        "owner_epoch": 2,
        "active": True,
    }
    assert "email" not in response.text
    assert "canonical_url" not in response.text
    assert "display_name" not in response.text


@pytest.mark.parametrize(
    ("error", "status_code", "code", "retryable"),
    [
        (
            W3AuthorityNotFoundError("AUTHORITY_CONTEXT_NOT_FOUND"),
            404,
            "W3_AUTHORITY_NOT_FOUND",
            False,
        ),
        (
            W3AuthorityConflictError("private database detail"),
            409,
            "W3_AUTHORITY_CONFLICT",
            False,
        ),
        (
            TimeoutError("private endpoint and token detail"),
            503,
            "W3_AUTHORITY_UNAVAILABLE",
            True,
        ),
    ],
)
def test_private_authority_maps_failures_to_safe_error_bodies(
    error: Exception,
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    with patch(
        "app.runtime.w3_authority_adapter.W3AuthorityService.current",
        side_effect=error,
    ):
        response = _client().post(
            "/internal/v1/w3/authority/current",
            headers=_headers(),
            json=_request(),
        )

    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert response.json()["retryable"] is retryable
    assert str(error) not in response.text
    assert "w3-authority-secret" not in response.text


def test_private_authority_rejects_extra_fields_before_lookup() -> None:
    body = _request() | {"owner_id": str(uuid4()), "active": True}
    with patch("app.runtime.w3_authority_adapter.W3AuthorityService.current") as current:
        response = _client().post(
            "/internal/v1/w3/authority/current",
            headers=_headers(),
            json=body,
        )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_PRIVATE_REQUEST"
    current.assert_not_called()


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "w3_authority_private_url": "http://w1-authority:8043",
        "w3_authority_database_url": (
            "postgresql+psycopg://w3_authority:placeholder@postgres/epick_local"
        ),
        "w3_authority_private_bearer": "placeholder-not-a-real-secret",
        "w3_authority_expected_principal": "epick-w3-core-runtime",
        "w3_authority_connect_timeout_seconds": 1,
        "w3_authority_read_timeout_seconds": 2,
        "w3_authority_total_timeout_seconds": 5,
    }
    values.update(changes)
    return Settings(_env_file=None, **values)


def test_w3_authority_runtime_settings_accept_complete_private_configuration() -> None:
    configured = _settings()

    assert configured.w3_authority_private_url == "http://w1-authority:8043"
    assert configured.w3_authority_private_bearer is not None
    assert configured.w3_authority_private_bearer.get_secret_value() == (
        "placeholder-not-a-real-secret"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"w3_authority_private_bearer": None},
        {"w3_authority_expected_principal": " "},
        {"w3_authority_connect_timeout_seconds": 3},
        {"w3_authority_total_timeout_seconds": 31},
        {"w3_authority_private_url": "https://authority.example.com"},
        {
            "w3_authority_database_url": (
                "postgresql+psycopg://epick:epick_2026_local@localhost:5432/epick_local"
            )
        },
    ],
)
def test_w3_authority_runtime_settings_reject_partial_or_unsafe_configuration(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _settings(**changes)
