from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta, timezone
from enum import StrEnum
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.responses import Response

import epick_engine.source_collection.api as api
from epick_engine.source_collection.api import (
    ApiBoundarySettings,
    ApiErrorCode,
    ApiErrorEnvelope,
    ApiProblem,
    AuthenticatedPrincipal,
    CursorCodec,
    build_operation_scope,
    canonical_request_hash,
    install_api_exception_handlers,
    make_configured_dependency,
    make_idempotency_key_dependency,
    make_principal_dependency,
    require_configured_dependency,
    require_owned_resource,
)
from epick_engine.source_collection.policy import (
    ExecutionPolicyUnconfigured,
    PolicyBlocked,
    UnsafeDestination,
    UnsupportedFormat,
)

_CURSOR_SECRET = "cursor-secret-not-for-production"
_CURSOR_SETTINGS = ApiBoundarySettings(
    idempotency_key_max_length=256,
    cursor_max_token_length=1_024,
)


def _error(response, *, status: int, code: ApiErrorCode) -> dict[str, object]:
    assert response.status_code == status
    envelope = ApiErrorEnvelope.model_validate(response.json())
    assert envelope.error.code is code
    assert UUID(str(envelope.error.correlation_id)).version == 4
    return response.json()["error"]


def _without_correlation(response) -> dict[str, object]:
    error = response.json()["error"].copy()
    error["correlation_id"] = "<server-generated>"
    return error


def _problem_status(problem: ApiProblem, code: ApiErrorCode, status: int) -> None:
    assert problem.code is code
    assert problem.definition.status_code == status


def _request(headers: list[tuple[str, str]] | None = None) -> Request:
    raw_headers = [
        (name.lower().encode("ascii"), value.encode("latin-1")) for name, value in (headers or [])
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": raw_headers,
            "client": ("test-client", 1),
            "server": ("test-server", 80),
        }
    )


def _render_exception(app: FastAPI, exception: Exception) -> httpx.Response:
    handler_type = next(
        candidate for candidate in type(exception).__mro__ if candidate in app.exception_handlers
    )
    handler = cast(
        Callable[[Request, Exception], Coroutine[Any, Any, Response]],
        app.exception_handlers[handler_type],
    )
    coroutine = handler(_request(), exception)
    try:
        coroutine.send(None)
    except StopIteration as completed:
        response = cast(Response, completed.value)
    else:
        pytest.fail("common exception handler must not depend on an event loop")
    finally:
        coroutine.close()
    return httpx.Response(
        status_code=response.status_code,
        content=response.body,
        headers=response.headers,
    )


def _render_problem(problem: ApiProblem) -> httpx.Response:
    app = FastAPI()
    install_api_exception_handlers(app)
    return _render_exception(app, problem)


def _cursor_codec(settings: ApiBoundarySettings = _CURSOR_SETTINGS) -> CursorCodec:
    return CursorCodec(_CURSOR_SECRET, settings)


def _cursor_arguments(owner_user_id: UUID) -> dict[str, object]:
    return {
        "owner_user_id": owner_user_id,
        "route_scope": "GET /api/v1/sources",
        "filter_context": {"company_id": "company-1", "active": True},
        "sort": "created_at:desc",
    }


def _replace_cursor_version(token: str, version: int) -> str:
    encoded_payload, _ = token.split(".")
    padding = "=" * (-len(encoded_payload) % 4)
    payload = json.loads(base64.urlsafe_b64decode(encoded_payload + padding))
    payload["v"] = version
    payload_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_CURSOR_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode("ascii")
    signed = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{encoded}.{signed}"


def _noncanonical_signature_variant(token: str) -> str:
    encoded_payload, encoded_signature = token.split(".")
    padding = "=" * (-len(encoded_signature) % 4)
    decoded_signature = base64.urlsafe_b64decode(encoded_signature + padding)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    for replacement in alphabet:
        candidate = encoded_signature[:-1] + replacement
        candidate_padding = "=" * (-len(candidate) % 4)
        if (
            candidate != encoded_signature
            and base64.urlsafe_b64decode(candidate + candidate_padding) == decoded_signature
        ):
            return f"{encoded_payload}.{candidate}"
    raise AssertionError("expected an equivalent non-canonical base64url representation")


def test_module_does_not_create_an_application_router_or_default_w1_dispatch() -> None:
    exported = vars(api)

    assert {"app", "router", "W1Dispatch"}.isdisjoint(exported)


def test_public_error_catalog_includes_all_adopted_conflict_codes_and_korean_action() -> None:
    assert {
        ApiErrorCode.STALE_INPUT,
        ApiErrorCode.CORE_DECISION_REQUIRED,
        ApiErrorCode.ACTION_NOT_ALLOWED,
        ApiErrorCode.IDEMPOTENCY_CONFLICT,
    } <= set(ApiErrorCode)

    response = _render_problem(ApiProblem(ApiErrorCode.CORE_DECISION_REQUIRED))
    error = _error(response, status=409, code=ApiErrorCode.CORE_DECISION_REQUIRED)

    assert error["message_ko"] == "핵심 자료의 누락 영향을 확인한 뒤 다시 요청해 주세요."
    assert error["actions"] == [{"code": "REVIEW_REQUEST", "label_ko": "요청 확인"}]


@pytest.mark.parametrize(
    ("exception", "code", "status"),
    [
        (PolicyBlocked("secret-policy-detail"), ApiErrorCode.SOURCE_POLICY_BLOCKED, 403),
        (UnsafeDestination("secret-url"), ApiErrorCode.UNSAFE_DESTINATION, 403),
        (
            ExecutionPolicyUnconfigured("secret-setting"),
            ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED,
            503,
        ),
        (UnsupportedFormat("secret-format"), ApiErrorCode.UNSUPPORTED_FORMAT, 422),
        (RuntimeError("secret-unexpected"), ApiErrorCode.DEPENDENCY_UNAVAILABLE, 503),
    ],
)
def test_domain_and_unexpected_errors_use_sanitized_common_envelopes(
    exception: Exception,
    code: ApiErrorCode,
    status: int,
) -> None:
    app = FastAPI()
    install_api_exception_handlers(app)

    response = _render_exception(app, exception)

    _error(response, status=status, code=code)
    assert str(exception) not in response.text


def test_exception_handler_installation_rejects_debug_apps() -> None:
    with pytest.raises(RuntimeError):
        install_api_exception_handlers(FastAPI(debug=True))


def test_api_problem_and_validation_error_use_the_same_exact_sanitized_envelope() -> None:
    app = FastAPI()
    install_api_exception_handlers(app)
    sentinel = "token=do-not-disclose"

    forced = _render_exception(app, ApiProblem(ApiErrorCode.INVALID_INPUT))
    invalid = _render_exception(app, RequestValidationError([], body=sentinel))

    expected = {
        "code": "INVALID_INPUT",
        "message_ko": "요청 입력을 확인해 주세요.",
        "retryable": False,
        "actions": [],
        "correlation_id": "<server-generated>",
    }
    for response in (forced, invalid):
        _error(response, status=422, code=ApiErrorCode.INVALID_INPUT)
        assert _without_correlation(response) == expected
        assert sentinel not in response.text


@pytest.mark.parametrize(
    ("authenticator", "expected_status", "expected_code"),
    [
        (None, 503, ApiErrorCode.DEPENDENCY_UNAVAILABLE),
        (lambda _request: None, 401, ApiErrorCode.AUTHENTICATION_REQUIRED),
        (
            lambda _request: (_ for _ in ()).throw(RuntimeError("auth-secret")),
            503,
            ApiErrorCode.DEPENDENCY_UNAVAILABLE,
        ),
        (lambda _request: object(), 503, ApiErrorCode.DEPENDENCY_UNAVAILABLE),
    ],
)
def test_principal_dependency_fails_closed_for_unavailable_invalid_or_missing_authentication(
    authenticator: object,
    expected_status: int,
    expected_code: ApiErrorCode,
) -> None:
    dependency = make_principal_dependency(
        cast(Callable[[Request], AuthenticatedPrincipal | None] | None, authenticator)
    )
    request = _request(
        [
            ("X-User-Id", str(uuid4())),
            ("Authorization", "Bearer auth-secret"),
        ]
    )
    with pytest.raises(ApiProblem) as caught:
        dependency(request)
    response = _render_problem(caught.value)

    _error(response, status=expected_status, code=expected_code)
    assert "auth-secret" not in response.text


def test_principal_dependency_uses_only_the_trusted_authenticator_result() -> None:
    trusted_user_id = uuid4()
    dependency = make_principal_dependency(lambda _request: AuthenticatedPrincipal(trusted_user_id))
    principal = dependency(_request([("X-User-Id", str(uuid4()))]))

    assert principal.user_id == trusted_user_id


def test_authenticated_principal_rejects_a_non_uuid_owner() -> None:
    with pytest.raises(ValueError):
        AuthenticatedPrincipal(cast(UUID, "not-a-uuid"))


def test_missing_and_foreign_owned_resources_have_indistinguishable_404_responses() -> None:
    owner_user_id = uuid4()
    observed_calls: list[tuple[UUID, UUID]] = []

    def load(*, resource_id: UUID, owner_user_id: UUID) -> None:
        observed_calls.append((resource_id, owner_user_id))
        return None

    principal = AuthenticatedPrincipal(owner_user_id)
    missing_id = uuid4()
    foreign_id = uuid4()
    responses: list[httpx.Response] = []
    for resource_id in (missing_id, foreign_id):
        with pytest.raises(ApiProblem) as caught:
            require_owned_resource(load, resource_id=resource_id, principal=principal)
        responses.append(_render_problem(caught.value))
    missing, foreign = responses

    _error(missing, status=404, code=ApiErrorCode.RESOURCE_NOT_FOUND)
    _error(foreign, status=404, code=ApiErrorCode.RESOURCE_NOT_FOUND)
    assert _without_correlation(missing) == _without_correlation(foreign)
    assert str(missing_id) not in missing.text and str(foreign_id) not in foreign.text
    assert observed_calls == [(missing_id, owner_user_id), (foreign_id, owner_user_id)]


def test_configured_dependency_guard_returns_only_an_explicit_dependency() -> None:
    dependency = object()

    assert require_configured_dependency(dependency) is dependency
    assert make_configured_dependency(dependency)() is dependency
    with pytest.raises(ApiProblem) as caught:
        require_configured_dependency(None)
    _problem_status(caught.value, ApiErrorCode.DEPENDENCY_UNAVAILABLE, 503)


@pytest.mark.parametrize(
    "settings",
    [
        None,
        ApiBoundarySettings(idempotency_key_max_length=1, cursor_max_token_length=1_024),
    ],
)
def test_missing_or_too_small_runtime_settings_fail_closed(
    settings: ApiBoundarySettings | None,
) -> None:
    dependency = make_idempotency_key_dependency(settings)
    with pytest.raises(ApiProblem) as caught:
        dependency(_request([("Idempotency-Key", "request-key")]))
    response = _render_problem(caught.value)

    if settings is None:
        _error(response, status=503, code=ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED)
    else:
        _error(response, status=422, code=ApiErrorCode.INVALID_INPUT)


@pytest.mark.parametrize(
    "values",
    [
        (0, 100),
        (257, 100),
        (True, 100),
        (100, 0),
        (100, True),
    ],
)
def test_invalid_boundary_settings_are_execution_policy_errors(
    values: tuple[object, object],
) -> None:
    with pytest.raises(ApiProblem) as caught:
        ApiBoundarySettings(
            idempotency_key_max_length=values[0],  # type: ignore[arg-type]
            cursor_max_token_length=values[1],  # type: ignore[arg-type]
        )

    _problem_status(caught.value, ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED, 503)


@pytest.mark.parametrize(
    ("default_limit", "max_limit"),
    [
        (None, 10),
        (10, None),
        (0, 10),
        (11, 10),
        (10, 0),
        (True, 10),
        (10, True),
        (10.0, 100),
        ("10", 100),
    ],
)
def test_invalid_pagination_settings_are_execution_policy_errors(
    default_limit: object,
    max_limit: object,
) -> None:
    with pytest.raises(ApiProblem) as caught:
        ApiBoundarySettings(
            idempotency_key_max_length=256,
            cursor_max_token_length=1_024,
            page_default_limit=default_limit,  # type: ignore[arg-type]
            page_max_limit=max_limit,  # type: ignore[arg-type]
        )

    _problem_status(caught.value, ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED, 503)


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Idempotency-Key", "   ")],
        [("Idempotency-Key", "first"), ("Idempotency-Key", "second")],
        [("Idempotency-Key", "x" * 17)],
    ],
)
def test_idempotency_key_rejects_missing_blank_duplicate_and_overlong_headers(
    headers: list[tuple[str, str]],
) -> None:
    dependency = make_idempotency_key_dependency(ApiBoundarySettings(16, 1_024))
    with pytest.raises(ApiProblem) as caught:
        dependency(_request(headers))
    response = _render_problem(caught.value)

    _error(response, status=422, code=ApiErrorCode.INVALID_INPUT)


def test_idempotency_key_only_trims_and_does_not_unicode_normalize() -> None:
    dependency = make_idempotency_key_dependency(ApiBoundarySettings(16, 1_024))

    def request_with(value: str) -> Request:
        headers = SimpleNamespace(getlist=lambda _name: [value])
        return cast(Request, SimpleNamespace(headers=headers))

    angstrom_sign = dependency(request_with("  Å  "))
    latin_a_ring = dependency(request_with("Å"))
    no_break_space = chr(0xA0)
    no_break_spaces = dependency(request_with(f"{no_break_space}key{no_break_space}"))

    assert (angstrom_sign, latin_a_ring) == ("Å", "Å")
    assert no_break_spaces == f"{no_break_space}key{no_break_space}"


class _State(StrEnum):
    QUEUED = "queued"


class _CanonicalPayload(BaseModel):
    user_id: UUID
    occurred_at: datetime
    effective_date: date
    state: _State


def test_canonical_request_hash_is_stable_across_json_equivalents_and_supported_values() -> None:
    user_id = uuid4()
    payload = _CanonicalPayload(
        user_id=user_id,
        occurred_at=datetime(2026, 9, 10, 9, 0, tzinfo=UTC),
        effective_date=date(2026, 9, 10),
        state=_State.QUEUED,
    )
    equivalent = {
        "state": "queued",
        "effective_date": date(2026, 9, 10),
        "occurred_at": datetime(2026, 9, 10, 18, 0, tzinfo=timezone(timedelta(hours=9))),
        "user_id": user_id,
    }

    assert canonical_request_hash(payload, 1) == canonical_request_hash(equivalent, 1)


def test_canonical_request_hash_is_sensitive_to_payload_and_input_version() -> None:
    original = canonical_request_hash({"source": "one"}, 1)

    assert original not in {
        canonical_request_hash({"source": "two"}, 1),
        canonical_request_hash({"source": "one"}, 2),
    }


def test_canonical_request_hash_requires_input_version_and_rejects_lone_surrogates() -> None:
    with pytest.raises(TypeError):
        canonical_request_hash({"source": "one"})  # type: ignore[call-arg]
    with pytest.raises(ApiProblem) as caught:
        canonical_request_hash({"value": chr(0xD800)}, 1)

    _problem_status(caught.value, ApiErrorCode.INVALID_INPUT, 422)


@pytest.mark.parametrize(
    ("payload", "input_version"),
    [
        ({"value": float("nan")}, 1),
        ({"value": float("inf")}, 1),
        ({"occurred_at": datetime(2026, 9, 10, 9, 0)}, 1),
        ({1: "not-a-string-key"}, 1),
        ({"value": "valid"}, 0),
        ({"value": "valid"}, True),
    ],
)
def test_canonical_request_hash_rejects_noncanonical_inputs(
    payload: object,
    input_version: object,
) -> None:
    with pytest.raises(ApiProblem) as caught:
        canonical_request_hash(payload, input_version)  # type: ignore[arg-type]

    _problem_status(caught.value, ApiErrorCode.INVALID_INPUT, 422)


def test_operation_scope_is_server_defined_and_rejects_request_derived_values() -> None:
    source_id = uuid4()
    valid = build_operation_scope("post", "/api/v1/sources/{source_id}", source_id)
    invalid = [
        ("POST", "https://example.test/sources", None),
        ("POST", "/sources", None),
        ("POST", "/api/v1/sources?cursor=untrusted", None),
        ("POST", "/api/v1/sources#fragment", None),
        ("POST", "/api/v1/sources with-space", None),
        ("POST", "/api/v1/" + "x" * 300, None),
        ("POST", "/api/v1/sources/{source_id}", None),
        ("POST", "/api/v1/sources/{source_id}", str(source_id)),
    ]

    assert valid == f"POST /api/v1/sources/{{source_id}}#{source_id}"
    for arguments in invalid:
        with pytest.raises(ApiProblem) as caught:
            build_operation_scope(*arguments)
        _problem_status(caught.value, ApiErrorCode.INVALID_INPUT, 422)


def test_cursor_round_trip_is_owner_bound_opaque_and_does_not_contain_the_secret() -> None:
    codec = _cursor_codec()
    owner_user_id = uuid4()
    arguments = _cursor_arguments(owner_user_id)
    position = {"sequence": 3}

    token = codec.encode(**arguments, position=position)
    decoded = codec.decode(token, **arguments)

    assert decoded == position
    assert str(owner_user_id) not in token and _CURSOR_SECRET not in token


def test_cursor_tamper_malformed_version_and_context_mismatches_are_indistinguishable() -> None:
    codec = _cursor_codec()
    owner_user_id = uuid4()
    arguments = _cursor_arguments(owner_user_id)
    token = codec.encode(**arguments, position={"sequence": 3})
    replacement = "A" if token[-1] != "A" else "B"
    tampered = token[:-1] + replacement
    invalid_attempts = [
        lambda: codec.decode(tampered, **arguments),
        lambda: codec.decode(_noncanonical_signature_variant(token), **arguments),
        lambda: codec.decode("not-a-cursor", **arguments),
        lambda: codec.decode(token[:5], **arguments),
        lambda: codec.decode(_replace_cursor_version(token, 2), **arguments),
        lambda: codec.decode(token, **_cursor_arguments(uuid4())),
        lambda: codec.decode(
            token,
            **{**arguments, "route_scope": "GET /api/v1/other-sources"},
        ),
        lambda: codec.decode(token, **{**arguments, "filter_context": {"company_id": "other"}}),
        lambda: codec.decode(token, **{**arguments, "sort": "created_at:asc"}),
    ]
    problems: list[ApiProblem] = []

    for attempt in invalid_attempts:
        with pytest.raises(ApiProblem) as caught:
            attempt()
        problems.append(caught.value)

    assert {(problem.code, problem.definition.status_code) for problem in problems} == {
        (ApiErrorCode.INVALID_CURSOR, 422)
    }


@pytest.mark.parametrize(
    "secret",
    [None, "", "   ", "x", b"", b"\t", b"x" * 31],
)
def test_cursor_requires_an_injected_nonblank_secret(secret: str | bytes | None) -> None:
    with pytest.raises(ApiProblem) as caught:
        CursorCodec(secret, _CURSOR_SETTINGS)

    _problem_status(caught.value, ApiErrorCode.DEPENDENCY_UNAVAILABLE, 503)


def test_cursor_requires_settings_and_enforces_the_configured_token_limit() -> None:
    with pytest.raises(ApiProblem) as missing_settings:
        CursorCodec(_CURSOR_SECRET, None)
    _problem_status(
        missing_settings.value,
        ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED,
        503,
    )

    with pytest.raises(ApiProblem) as too_small:
        ApiBoundarySettings(256, 1)
    _problem_status(
        too_small.value,
        ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED,
        503,
    )

    codec = _cursor_codec(ApiBoundarySettings(256, 1_024))
    with pytest.raises(ApiProblem) as too_long:
        codec.encode(
            **_cursor_arguments(uuid4()),
            position={"sequence": "x" * 2_000},
        )
    _problem_status(
        too_long.value,
        ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED,
        503,
    )


def test_invalid_cursor_response_does_not_reflect_its_token_or_secret() -> None:
    codec = _cursor_codec()
    owner_user_id = uuid4()
    arguments = _cursor_arguments(owner_user_id)
    sent_token = "malformed-cursor-token"
    with pytest.raises(ApiProblem) as caught:
        codec.decode(sent_token, **arguments)
    response = _render_problem(caught.value)

    _error(response, status=422, code=ApiErrorCode.INVALID_CURSOR)
    assert sent_token not in response.text and _CURSOR_SECRET not in response.text
