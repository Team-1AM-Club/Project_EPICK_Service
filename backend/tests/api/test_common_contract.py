from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import InvalidInputError
from app.api.idempotency import canonical_request_hash, parse_if_match, require_idempotency_key
from app.api.pagination import CursorCodec, validate_page_limit
from app.api.schemas.common import ApiErrorResponse, CursorListResponse
from tests.api.conftest import override_principal


def test_api_404_uses_safe_error_envelope_and_correlation_id(api_client: TestClient) -> None:
    request_id = "2a6f97c3-9565-4e23-a7df-d0ddaea4c770"

    response = api_client.get("/api/v1/not-found", headers={"X-Request-ID": request_id})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {
        "error": {
            "code": "RESOURCE_NOT_FOUND",
            "message_ko": "요청한 리소스를 찾을 수 없습니다.",
            "retryable": False,
            "actions": [],
            "correlation_id": request_id,
            "fields": [],
        }
    }


def test_api_validation_uses_common_error_envelope(api_client: TestClient) -> None:
    response = api_client.post("/api/v1/_test/validation", json={})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_INPUT"
    assert error["fields"] == [{"field": "body.name", "reason": "MISSING"}]
    UUID(error["correlation_id"])
    assert response.headers["X-Request-ID"] == error["correlation_id"]


def test_api_default_principal_provider_fails_closed(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/_test/principal")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_test_override_supplies_only_the_internal_owner(
    api_app: FastAPI, owner_one_id: UUID
) -> None:
    override_principal(api_app, owner_one_id)

    with TestClient(api_app) as client:
        response = client.get("/api/v1/_test/principal")

    assert response.status_code == 200
    assert response.json() == {"owner_user_id": str(owner_one_id)}


def test_cursor_is_opaque_signed_and_page_limit_is_bounded() -> None:
    codec = CursorCodec("test-only-signing-key")
    cursor = codec.encode({"created_at": "2026-09-16T00:00:00Z", "id": "example"})

    assert codec.decode(cursor) == {"created_at": "2026-09-16T00:00:00Z", "id": "example"}
    assert validate_page_limit(1) == 1
    assert validate_page_limit(100) == 100
    with pytest.raises(InvalidInputError):
        codec.decode(cursor[:-1] + "A")
    with pytest.raises(InvalidInputError):
        validate_page_limit(101)


def test_cursor_decode_handles_a_separator_byte_inside_the_binary_signature() -> None:
    codec = CursorCodec("test-key")
    payload = {
        "resource": "recommendation-candidates:10000000-0000-4000-8000-000000000002",
        "offset": 1,
    }

    assert codec.decode(codec.encode(payload)) == payload


def test_idempotency_and_if_match_helpers_are_deterministic() -> None:
    assert require_idempotency_key(" key-1 ") == "key-1"
    assert canonical_request_hash({"a": 1, "b": ["x"]}) == canonical_request_hash(
        {"b": ["x"], "a": 1}
    )
    assert parse_if_match('"3"') == 3
    assert parse_if_match("4") == 4
    with pytest.raises(InvalidInputError):
        require_idempotency_key(None)
    with pytest.raises(InvalidInputError):
        parse_if_match('W/"3"')


def test_common_schemas_remain_the_public_list_and_error_contract() -> None:
    assert CursorListResponse[str](items=["first"], next_cursor=None).model_dump() == {
        "items": ["first"],
        "next_cursor": None,
    }
    schema = ApiErrorResponse.model_json_schema()
    assert "error" in schema["properties"]
