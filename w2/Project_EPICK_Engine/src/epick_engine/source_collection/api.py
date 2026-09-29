"""Reusable, fail-closed FastAPI boundary primitives for source collection."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol, TypeVar, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from epick_engine.source_collection.contracts import DateValue, SourceType
from epick_engine.source_collection.policy import (
    ExecutionPolicyUnconfigured,
    PolicyBlocked,
    UnsafeDestination,
    UnsupportedFormat,
)
from epick_engine.source_collection.service import (
    CompanyNotFound,
    CompanyResolutionIdempotencyConflict,
    CompanyResolutionIdempotencyUnavailable,
    InvalidCompanyCursor,
    JobPostingContentUnavailable,
    JobPostingNotFound,
    JobPostingSelectionInvalid,
    JobPostingView,
    RetainedBodyReference,
    SourceRefreshIdempotencyConflict,
    SourceVersionEvidenceDependencyUnavailable,
    SourceVersionEvidenceInvalidInput,
    SourceVersionEvidenceNotFound,
    canonicalize_source_url,
    normalize_company_domain,
)

_DATABASE_IDEMPOTENCY_KEY_MAX_LENGTH = 256
_DATABASE_OPERATION_MAX_LENGTH = 256
_API_ROUTE_PREFIX = "/api/v1"
_CURSOR_VERSION = 1
_CURSOR_SECRET_MIN_BYTES = 32
_COMPANY_SEARCH_ROUTE_SCOPE = "GET /api/v1/companies/search"
_COMPANY_SEARCH_SORT = "registry:ascending"
_COMPANY_SOURCE_LIST_ROUTE_SCOPE = "GET /api/v1/companies/{company_id}/sources"
_COMPANY_SOURCE_LIST_SORT = "source:ascending"
_SOURCE_VERSION_LIST_ROUTE_SCOPE = "GET /api/v1/sources/{source_id}/versions"
_SOURCE_VERSION_LIST_SORT = "collected_at,source_version_id:descending"
_SOURCE_EVIDENCE_LIST_ROUTE_SCOPE = "GET /api/v1/source-versions/{version_id}/evidence"
_SOURCE_EVIDENCE_LIST_SORT = "chunk_order,evidence_id:ascending"
_BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_METHOD_PATTERN = re.compile(r"^[A-Za-z]+$")
_MIN_CURSOR_TOKEN_LENGTH = (
    len(
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "f": "0" * 64,
                    "o": "0" * 64,
                    "p": None,
                    "r": "GET /api/v1/x",
                    "s": "x",
                    "v": _CURSOR_VERSION,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).rstrip(b"=")
    )
    + 1
    + len(base64.urlsafe_b64encode(bytes(hashlib.sha256().digest_size)).rstrip(b"="))
)


class ApiErrorCode(StrEnum):
    """Stable public error codes allowed at the common API boundary."""

    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_CURSOR = "INVALID_CURSOR"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    EXECUTION_POLICY_UNCONFIGURED = "EXECUTION_POLICY_UNCONFIGURED"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    SOURCE_POLICY_BLOCKED = "SOURCE_POLICY_BLOCKED"
    UNSAFE_DESTINATION = "UNSAFE_DESTINATION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    STALE_INPUT = "STALE_INPUT"
    CORE_DECISION_REQUIRED = "CORE_DECISION_REQUIRED"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiAction(_ApiModel):
    code: str = Field(min_length=1)
    label_ko: str = Field(min_length=1)


class ApiError(_ApiModel):
    code: ApiErrorCode
    message_ko: str = Field(min_length=1)
    retryable: bool
    actions: tuple[ApiAction, ...]
    correlation_id: UUID


class ApiErrorEnvelope(_ApiModel):
    error: ApiError


@dataclass(frozen=True, slots=True)
class _ProblemDefinition:
    status_code: int
    message_ko: str
    retryable: bool
    actions: tuple[ApiAction, ...] = ()


_PROBLEMS: Mapping[ApiErrorCode, _ProblemDefinition] = {
    ApiErrorCode.AUTHENTICATION_REQUIRED: _ProblemDefinition(401, "인증이 필요합니다.", False),
    ApiErrorCode.RESOURCE_NOT_FOUND: _ProblemDefinition(
        404, "요청한 자료를 찾을 수 없습니다.", False
    ),
    ApiErrorCode.INVALID_INPUT: _ProblemDefinition(422, "요청 입력을 확인해 주세요.", False),
    ApiErrorCode.INVALID_CURSOR: _ProblemDefinition(
        422, "페이지 조회 정보를 확인해 주세요.", False
    ),
    ApiErrorCode.DEPENDENCY_UNAVAILABLE: _ProblemDefinition(
        503, "필수 의존 시스템을 사용할 수 없습니다.", True
    ),
    ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED: _ProblemDefinition(
        503, "필수 실행 정책이 설정되지 않았습니다.", True
    ),
    ApiErrorCode.UNSUPPORTED_FORMAT: _ProblemDefinition(
        422, "지원하지 않는 자료 형식입니다.", False
    ),
    ApiErrorCode.SOURCE_POLICY_BLOCKED: _ProblemDefinition(
        403, "현재 정책으로는 이 자료를 수집할 수 없습니다.", False
    ),
    ApiErrorCode.UNSAFE_DESTINATION: _ProblemDefinition(
        403, "안전하지 않은 목적지로의 요청이 차단되었습니다.", False
    ),
    ApiErrorCode.IDEMPOTENCY_CONFLICT: _ProblemDefinition(
        409, "같은 멱등성 키가 다른 요청에 이미 사용되었습니다.", False
    ),
    ApiErrorCode.STALE_INPUT: _ProblemDefinition(
        409, "입력 상태가 변경되었습니다. 최신 상태를 확인한 뒤 다시 요청해 주세요.", False
    ),
    ApiErrorCode.CORE_DECISION_REQUIRED: _ProblemDefinition(
        409,
        "핵심 자료의 누락 영향을 확인한 뒤 다시 요청해 주세요.",
        False,
        (ApiAction(code="REVIEW_REQUEST", label_ko="요청 확인"),),
    ),
    ApiErrorCode.ACTION_NOT_ALLOWED: _ProblemDefinition(
        409, "현재 상태에서는 요청한 행동을 수행할 수 없습니다.", False
    ),
}


class ApiProblem(Exception):
    """A public problem selected only from the fixed safe catalogue."""

    def __init__(self, code: ApiErrorCode) -> None:
        self.code = code
        self.definition = _PROBLEMS[code]
        super().__init__(code.value)


def _problem_response(problem: ApiProblem) -> JSONResponse:
    definition = problem.definition
    envelope = ApiErrorEnvelope(
        error=ApiError(
            code=problem.code,
            message_ko=definition.message_ko,
            retryable=definition.retryable,
            actions=definition.actions,
            correlation_id=uuid4(),
        )
    )
    return JSONResponse(
        status_code=definition.status_code,
        content=envelope.model_dump(mode="json"),
    )


def install_api_exception_handlers(app: FastAPI) -> None:
    """Install the common sanitized error envelope on an owner-provided app."""

    if app.debug:
        raise RuntimeError("common API exception handlers require debug mode to be disabled")

    async def handle_api_problem(_: Request, problem: Exception) -> JSONResponse:
        return _problem_response(cast(ApiProblem, problem))

    async def handle_validation_error(_: Request, __: Exception) -> JSONResponse:
        return _problem_response(ApiProblem(ApiErrorCode.INVALID_INPUT))

    def domain_handler(
        code: ApiErrorCode,
    ) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
        async def handle_domain_problem(_: Request, __: Exception) -> JSONResponse:
            return _problem_response(ApiProblem(code))

        return handle_domain_problem

    async def handle_unexpected_error(_: Request, __: Exception) -> JSONResponse:
        return _problem_response(ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE))

    app.add_exception_handler(ApiProblem, handle_api_problem)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(PolicyBlocked, domain_handler(ApiErrorCode.SOURCE_POLICY_BLOCKED))
    app.add_exception_handler(UnsafeDestination, domain_handler(ApiErrorCode.UNSAFE_DESTINATION))
    app.add_exception_handler(
        ExecutionPolicyUnconfigured,
        domain_handler(ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED),
    )
    app.add_exception_handler(UnsupportedFormat, domain_handler(ApiErrorCode.UNSUPPORTED_FORMAT))
    app.add_exception_handler(Exception, handle_unexpected_error)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise ValueError("authenticated principal user_id must be a UUID")


class PrincipalAuthenticator(Protocol):
    def __call__(self, request: Request) -> AuthenticatedPrincipal | None: ...


def make_principal_dependency(
    authenticator: PrincipalAuthenticator | None,
) -> Callable[[Request], AuthenticatedPrincipal]:
    """Create a dependency that never falls back to a client-supplied identity."""

    def require_principal(request: Request) -> AuthenticatedPrincipal:
        if authenticator is None:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        try:
            principal = authenticator(request)
        except Exception as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        if principal is None:
            raise ApiProblem(ApiErrorCode.AUTHENTICATION_REQUIRED)
        if not isinstance(principal, AuthenticatedPrincipal) or not isinstance(
            principal.user_id, UUID
        ):
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        return principal

    return require_principal


ResourceT_co = TypeVar("ResourceT_co", covariant=True)


class OwnedResourceLoader(Protocol[ResourceT_co]):
    def __call__(self, *, resource_id: UUID, owner_user_id: UUID) -> ResourceT_co | None: ...


def require_owned_resource[ResourceT](
    loader: OwnedResourceLoader[ResourceT],
    *,
    resource_id: UUID,
    principal: AuthenticatedPrincipal,
) -> ResourceT:
    """Load by resource and owner in one operation and collapse misses to one 404."""

    resource = loader(resource_id=resource_id, owner_user_id=principal.user_id)
    if resource is None:
        raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
    return resource


def require_configured_dependency[DependencyT](dependency: DependencyT | None) -> DependencyT:
    if dependency is None:
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
    return dependency


def make_configured_dependency[DependencyT](
    dependency: DependencyT | None,
) -> Callable[[], DependencyT]:
    def require_dependency() -> DependencyT:
        return require_configured_dependency(dependency)

    return require_dependency


@dataclass(frozen=True, slots=True)
class ApiBoundarySettings:
    """Limits supplied by the owner-managed execution policy (G-07)."""

    idempotency_key_max_length: int
    cursor_max_token_length: int
    page_default_limit: int | None = None
    page_max_limit: int | None = None

    def __post_init__(self) -> None:
        idempotency_limit = self.idempotency_key_max_length
        cursor_limit = self.cursor_max_token_length
        page_default_limit = self.page_default_limit
        page_max_limit = self.page_max_limit
        invalid_idempotency = (
            isinstance(idempotency_limit, bool)
            or not isinstance(idempotency_limit, int)
            or idempotency_limit < 1
            or idempotency_limit > _DATABASE_IDEMPOTENCY_KEY_MAX_LENGTH
        )
        invalid_cursor = (
            isinstance(cursor_limit, bool)
            or not isinstance(cursor_limit, int)
            or cursor_limit < _MIN_CURSOR_TOKEN_LENGTH
        )
        pagination_supplied = page_default_limit is not None or page_max_limit is not None
        invalid_pagination = False
        if pagination_supplied:
            if page_default_limit is None or page_max_limit is None:
                invalid_pagination = True
            elif (
                isinstance(page_default_limit, bool)
                or not isinstance(page_default_limit, int)
                or isinstance(page_max_limit, bool)
                or not isinstance(page_max_limit, int)
            ):
                invalid_pagination = True
            else:
                invalid_pagination = (
                    page_default_limit < 1
                    or page_max_limit < 1
                    or page_default_limit > page_max_limit
                )
        if invalid_idempotency or invalid_cursor or invalid_pagination:
            raise ApiProblem(ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED)


def _require_settings(settings: ApiBoundarySettings | None) -> ApiBoundarySettings:
    if not isinstance(settings, ApiBoundarySettings):
        raise ApiProblem(ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED)
    return settings


def _resolve_page_limit(
    settings: ApiBoundarySettings | None,
    requested_limit: int | None,
) -> int:
    configured = _require_settings(settings)
    default_limit = configured.page_default_limit
    max_limit = configured.page_max_limit
    if default_limit is None or max_limit is None:
        raise ApiProblem(ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED)
    if requested_limit is None:
        return default_limit
    if requested_limit > max_limit:
        raise ApiProblem(ApiErrorCode.INVALID_INPUT)
    return requested_limit


def make_idempotency_key_dependency(
    settings: ApiBoundarySettings | None,
) -> Callable[[Request], str]:
    def require_idempotency_key(request: Request) -> str:
        configured = _require_settings(settings)
        values = request.headers.getlist("Idempotency-Key")
        if len(values) != 1:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT)
        key = values[0].strip(" 	")
        if not key or len(key) > configured.idempotency_key_max_length:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT)
        return key

    return require_idempotency_key


def _normalize_json(value: object) -> object:
    if isinstance(value, BaseModel):
        return _normalize_json(value.model_dump(mode="python", by_alias=True))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT)
        normalized = value.astimezone(UTC).isoformat(timespec="microseconds")
        return normalized.replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ApiProblem(ApiErrorCode.INVALID_INPUT)
        return value
    if isinstance(value, Mapping):
        normalized_mapping: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ApiProblem(ApiErrorCode.INVALID_INPUT)
            normalized_mapping[key] = _normalize_json(item)
        return normalized_mapping
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_json(item) for item in value]
    raise ApiProblem(ApiErrorCode.INVALID_INPUT)


def _canonical_json_bytes(value: object) -> bytes:
    normalized = _normalize_json(value)
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return encoded.encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ApiProblem(ApiErrorCode.INVALID_INPUT) from exc


def canonical_request_hash(payload: object, input_version: int) -> str:
    """Hash validated request data with stable JSON and explicit input revision."""

    if isinstance(input_version, bool) or not isinstance(input_version, int) or input_version < 1:
        raise ApiProblem(ApiErrorCode.INVALID_INPUT)
    document = {"input_version": input_version, "payload": payload}
    return hashlib.sha256(_canonical_json_bytes(document)).hexdigest()


def build_operation_scope(
    method: str,
    route_template: str,
    target_ref: UUID | None = None,
) -> str:
    """Build a stable server-defined idempotency operation, never from a request URL."""

    if not isinstance(method, str) or not _METHOD_PATTERN.fullmatch(method):
        raise ApiProblem(ApiErrorCode.INVALID_INPUT)
    if not isinstance(route_template, str) or not _safe_route_template(route_template):
        raise ApiProblem(ApiErrorCode.INVALID_INPUT)
    if ("{" in route_template or "}" in route_template) and target_ref is None:
        raise ApiProblem(ApiErrorCode.INVALID_INPUT)
    operation = f"{method.upper()} {route_template}"
    if target_ref is not None:
        if not isinstance(target_ref, UUID):
            raise ApiProblem(ApiErrorCode.INVALID_INPUT)
        operation = f"{operation}#{target_ref}"
    if len(operation) > _DATABASE_OPERATION_MAX_LENGTH:
        raise ApiProblem(ApiErrorCode.INVALID_INPUT)
    return operation


def _safe_route_template(route_template: str) -> bool:
    return (
        (route_template == _API_ROUTE_PREFIX or route_template.startswith(f"{_API_ROUTE_PREFIX}/"))
        and "://" not in route_template
        and "?" not in route_template
        and "#" not in route_template
        and "\\" not in route_template
        and not any(character.isspace() or ord(character) < 32 for character in route_template)
    )


def _safe_route_scope(route_scope: str) -> bool:
    try:
        method, route = route_scope.split(" ", maxsplit=1)
    except ValueError:
        return False
    return (
        method == method.upper()
        and bool(_METHOD_PATTERN.fullmatch(method))
        and _safe_route_template(route)
        and len(route_scope) <= _DATABASE_OPERATION_MAX_LENGTH
    )


class CursorCodec:
    """Sign owner- and query-bound cursor state using an injected secret."""

    def __init__(
        self,
        secret: str | bytes | None,
        settings: ApiBoundarySettings | None,
    ) -> None:
        configured = _require_settings(settings)
        if isinstance(secret, str):
            secret_bytes = secret.encode("utf-8")
        elif isinstance(secret, bytes):
            secret_bytes = secret
        else:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        if len(secret_bytes) < _CURSOR_SECRET_MIN_BYTES or not secret_bytes.strip():
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        self._secret = secret_bytes
        self._max_token_length = configured.cursor_max_token_length

    def encode(
        self,
        *,
        owner_user_id: UUID,
        route_scope: str,
        filter_context: Mapping[str, object],
        sort: str,
        position: object,
    ) -> str:
        payload = self._cursor_payload(
            owner_user_id=owner_user_id,
            route_scope=route_scope,
            filter_context=filter_context,
            sort=sort,
            position=position,
        )
        try:
            payload_bytes = _canonical_json_bytes(payload)
        except ApiProblem as exc:
            raise _invalid_cursor() from exc
        signature = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        token = f"{_base64url_encode(payload_bytes)}.{_base64url_encode(signature)}"
        if len(token) > self._max_token_length:
            raise ApiProblem(ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED)
        return token

    def decode(
        self,
        token: str,
        *,
        owner_user_id: UUID,
        route_scope: str,
        filter_context: Mapping[str, object],
        sort: str,
    ) -> object:
        if not isinstance(token, str) or not token or len(token) > self._max_token_length:
            raise _invalid_cursor()
        try:
            encoded_payload, encoded_signature = token.split(".")
            payload_bytes = _base64url_decode(encoded_payload)
            supplied_signature = _base64url_decode(encoded_signature)
        except (ValueError, binascii.Error) as exc:
            raise _invalid_cursor() from exc
        expected_signature = hmac.new(self._secret, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise _invalid_cursor()
        try:
            payload = json.loads(payload_bytes)
            expected = self._cursor_payload(
                owner_user_id=owner_user_id,
                route_scope=route_scope,
                filter_context=filter_context,
                sort=sort,
                position=payload.get("p") if isinstance(payload, dict) else None,
            )
        except (ApiProblem, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise _invalid_cursor() from exc
        if not isinstance(payload, dict) or set(payload) != set(expected):
            raise _invalid_cursor()
        if payload != expected:
            raise _invalid_cursor()
        return payload["p"]

    def _cursor_payload(
        self,
        *,
        owner_user_id: UUID,
        route_scope: str,
        filter_context: Mapping[str, object],
        sort: str,
        position: object,
    ) -> dict[str, object]:
        if not isinstance(owner_user_id, UUID):
            raise _invalid_cursor()
        if not isinstance(route_scope, str) or not _safe_route_scope(route_scope):
            raise _invalid_cursor()
        if not isinstance(sort, str) or not sort:
            raise _invalid_cursor()
        try:
            filter_hash = hashlib.sha256(_canonical_json_bytes(filter_context)).hexdigest()
            normalized_position = _normalize_json(position)
        except ApiProblem as exc:
            raise _invalid_cursor() from exc
        owner_binding = hmac.new(
            self._secret, b"owner:" + owner_user_id.bytes, hashlib.sha256
        ).hexdigest()
        return {
            "f": filter_hash,
            "o": owner_binding,
            "p": normalized_position,
            "r": route_scope,
            "s": sort,
            "v": _CURSOR_VERSION,
        }


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if not value or not _BASE64URL_PATTERN.fullmatch(value):
        raise ValueError("invalid base64url")
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    if _base64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url")
    return decoded


def _invalid_cursor() -> ApiProblem:
    return ApiProblem(ApiErrorCode.INVALID_CURSOR)


class _CompanySearchParameters(_ApiModel):
    q: str = Field(min_length=1)
    official_domain: str | None = Field(default=None, min_length=1)
    cursor: str | None = Field(default=None, min_length=1)
    limit: int | None = Field(default=None, ge=1)

    @field_validator("q", "cursor")
    @classmethod
    def _reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("blank text is not allowed")
        return value

    @field_validator("official_domain")
    @classmethod
    def _normalize_official_domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_company_domain(value)
        except ValueError as exc:
            raise ValueError("official domain is invalid") from exc


class _CompanyResolutionBody(_ApiModel):
    name: str | None = Field(default=None, min_length=1)
    official_identifiers: list[str] | None = None
    selected_company_id: UUID | None = None
    identity_evidence_refs: list[str] | None = None

    @field_validator("name")
    @classmethod
    def _reject_blank_name(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("blank name is not allowed")
        return value

    @field_validator("official_identifiers", "identity_evidence_refs")
    @classmethod
    def _reject_blank_references(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and any(not value.strip() for value in values):
            raise ValueError("blank references are not allowed")
        return values

    @field_validator("identity_evidence_refs")
    @classmethod
    def _validate_evidence_urls(cls, values: list[str] | None) -> list[str] | None:
        for value in values or ():
            if "://" not in value:
                continue
            try:
                canonicalize_source_url(value)
            except ValueError as exc:
                raise ValueError("identity evidence URL is invalid") from exc
        return values


class _CompanyOutputModel(_ApiModel):
    """Fail closed when a service response is not the exact public contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _canonical_uuid_text(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ValueError("identifier must be a canonical UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ValueError("identifier must be a canonical UUID")
    return canonical


class _CompanyCandidateResponse(_CompanyOutputModel):
    company_id: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    identity_evidence_refs: list[Annotated[str, Field(min_length=1)]]

    @field_validator("company_id")
    @classmethod
    def _validate_company_id(cls, value: str) -> str:
        return _canonical_uuid_text(value)


class _CompanySearchServiceResponse(_CompanyOutputModel):
    candidates: list[_CompanyCandidateResponse]
    selection_required: bool
    next_cursor: int | None = Field(default=None, ge=0)


class _CompanySearchResponse(_CompanyOutputModel):
    candidates: list[_CompanyCandidateResponse]
    selection_required: bool
    next_cursor: str | None = Field(default=None, min_length=1)


class _CompanyResolutionResponse(_CompanyOutputModel):
    resolution: Literal["resolved", "selection_required", "unverified"]
    company_id: str | None = Field(default=None, min_length=1)
    candidates: list[_CompanyCandidateResponse]

    @field_validator("company_id")
    @classmethod
    def _validate_company_id(cls, value: str | None) -> str | None:
        return _canonical_uuid_text(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_resolution_shape(self) -> _CompanyResolutionResponse:
        has_company_id = self.company_id is not None
        has_candidates = bool(self.candidates)
        valid_shape = (
            (self.resolution == "resolved" and has_company_id and not has_candidates)
            or (self.resolution == "selection_required" and not has_company_id and has_candidates)
            or (self.resolution == "unverified" and not has_company_id and not has_candidates)
        )
        if not valid_shape:
            raise ValueError("resolution response state is inconsistent")
        return self


class _CompanyDetailResponse(_CompanyOutputModel):
    company_id: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    aliases: list[str]
    official_domains: list[str]
    legal_identifiers: dict[
        Annotated[str, Field(min_length=1)],
        Annotated[str, Field(min_length=1)],
    ]
    identity_status: str = Field(min_length=1)
    identity_evidence: list[str]

    @field_validator("company_id")
    @classmethod
    def _validate_company_id(cls, value: str) -> str:
        return _canonical_uuid_text(value)

    @field_validator("legal_identifiers")
    @classmethod
    def _validate_legal_identifiers(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not key.strip() or not identifier.strip() for key, identifier in value.items()):
            raise ValueError("legal identifiers must not contain blank keys or values")
        return value


class _CompanyRelationshipResponse(_CompanyOutputModel):
    relationship_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    related_company_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    valid_from: DateValue | None = None
    valid_to: DateValue | None = None

    @field_validator("relationship_id", "company_id", "related_company_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        return _canonical_uuid_text(value)


class _CompanyDetailServiceResponse(_CompanyOutputModel):
    company: _CompanyDetailResponse
    relationships: list[_CompanyRelationshipResponse]


class _SourceRegistrationBody(_ApiModel):
    url: str = Field(min_length=1)
    source_type: SourceType
    analysis_request_id: str | None = Field(default=None, min_length=1)

    @field_validator("url")
    @classmethod
    def _canonicalize_url(cls, value: str) -> str:
        try:
            return canonicalize_source_url(value)
        except ValueError as exc:
            raise ValueError("source URL is invalid") from exc

    @field_validator("analysis_request_id")
    @classmethod
    def _reject_blank_analysis_request_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("blank text is not allowed")
        return value


class _SourceListParameters(_ApiModel):
    cursor: str | None = Field(default=None, min_length=1)
    limit: int | None = Field(default=None, ge=1)

    @field_validator("cursor")
    @classmethod
    def _reject_blank_cursor(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("blank cursor is not allowed")
        return value


class _SourceOutputModel(_ApiModel):
    """Fail closed to the explicitly approved public Source fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _SourcePolicyResponse(_SourceOutputModel):
    official_status: str = Field(min_length=1)
    access_class: str = Field(min_length=1)
    collection_permission: str = Field(min_length=1)
    excerpt_storage_permission: str = Field(min_length=1)
    body_storage_permission: str = Field(min_length=1)
    redistribution_permission: str = Field(min_length=1)
    revision: int = Field(ge=1)


class _SourceObservationResponse(_SourceOutputModel):
    acquisition_status: str | None = Field(default=None, min_length=1)
    error_code: str | None = Field(default=None, min_length=1)
    access_class: str | None = Field(default=None, min_length=1)
    http_status: int | None = Field(default=None, ge=100, le=599)
    checked_url: str | None = Field(default=None, min_length=1)
    representation: str | None = Field(default=None, min_length=1)


class _SourceRestrictionResponse(_SourceOutputModel):
    state: str | None = Field(default=None, min_length=1)
    stage: str | None = Field(default=None, min_length=1)
    code: str | None = Field(default=None, min_length=1)
    impact: str | None = Field(default=None, min_length=1)
    restriction_status: str | None = Field(default=None, min_length=1)
    accuracy_status: str | None = Field(default=None, min_length=1)
    reason_code: str | None = Field(default=None, min_length=1)
    replacement_ref: str | None = Field(default=None, min_length=1)


class _SourceFreshnessResponse(_SourceOutputModel):
    state: str | None = Field(default=None, min_length=1)
    freshness_status: str | None = Field(default=None, min_length=1)


class _SourceActionResponse(_SourceOutputModel):
    action: str | None = Field(default=None, min_length=1)
    code: str | None = Field(default=None, min_length=1)
    label_ko: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_action_identifier(self) -> _SourceActionResponse:
        if self.action is None and self.code is None:
            raise ValueError("source action identifier is required")
        return self


class _SourceVersionSummaryResponse(_SourceOutputModel):
    source_version_id: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    source_type: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    hash_profile_version: str = Field(min_length=1)
    representation: str = Field(min_length=1)
    collected_at: datetime

    @field_validator("source_version_id")
    @classmethod
    def _validate_source_version_id(cls, value: str) -> str:
        return _canonical_uuid_text(value)


class _SourceResponse(_SourceOutputModel):
    source_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    policy: _SourcePolicyResponse
    current_observation: _SourceObservationResponse | None = None
    latest_available_version: _SourceVersionSummaryResponse | None = None
    current_restriction: _SourceRestrictionResponse
    freshness: _SourceFreshnessResponse
    actions: list[_SourceActionResponse]

    @field_validator("source_id", "company_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        return _canonical_uuid_text(value)


class _SourceRegistrationResponse(_SourceOutputModel):
    source_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    status_url: str = Field(min_length=1)

    @field_validator("source_id", "job_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        return _canonical_uuid_text(value)

    @model_validator(mode="after")
    def _validate_status_url(self) -> _SourceRegistrationResponse:
        if self.status_url != f"{_API_ROUTE_PREFIX}/jobs/{self.job_id}":
            raise ValueError("status URL must identify the accepted job")
        return self


class _SourceListServiceResponse(_SourceOutputModel):
    items: list[_SourceResponse]
    next_cursor: int | None = Field(default=None, ge=0)


class _SourceListResponse(_SourceOutputModel):
    items: list[_SourceResponse]
    next_cursor: str | None = Field(default=None, min_length=1)


class _SourceDetailResponse(_SourceOutputModel):
    source: _SourceResponse


_SOURCE_PUBLIC_FIELDS = (
    "source_id",
    "company_id",
    "url",
    "source_type",
    "policy",
    "current_observation",
    "latest_available_version",
    "current_restriction",
    "freshness",
    "actions",
)
_SOURCE_POLICY_PUBLIC_FIELDS = (
    "official_status",
    "access_class",
    "collection_permission",
    "excerpt_storage_permission",
    "body_storage_permission",
    "redistribution_permission",
    "revision",
)
_SOURCE_OBSERVATION_PUBLIC_FIELDS = (
    "acquisition_status",
    "error_code",
    "access_class",
    "http_status",
    "checked_url",
    "representation",
)
_SOURCE_RESTRICTION_PUBLIC_FIELDS = (
    "state",
    "stage",
    "code",
    "impact",
    "restriction_status",
    "accuracy_status",
    "reason_code",
    "replacement_ref",
)
_SOURCE_FRESHNESS_PUBLIC_FIELDS = ("state", "freshness_status")
_SOURCE_ACTION_PUBLIC_FIELDS = ("action", "code", "label_ko")
_SOURCE_VERSION_PUBLIC_FIELDS = (
    "source_version_id",
    "title",
    "source_type",
    "canonical_url",
    "content_hash",
    "hash_profile_version",
    "representation",
    "collected_at",
)


def _allow_list_mapping(payload: object, fields: Sequence[str]) -> object:
    if not isinstance(payload, Mapping):
        return payload
    return {field: payload[field] for field in fields if field in payload}


def _allow_list_source_payload(payload: object) -> object:
    source = _allow_list_mapping(payload, _SOURCE_PUBLIC_FIELDS)
    if not isinstance(source, dict):
        return source
    for field, fields in (
        ("policy", _SOURCE_POLICY_PUBLIC_FIELDS),
        ("current_observation", _SOURCE_OBSERVATION_PUBLIC_FIELDS),
        ("latest_available_version", _SOURCE_VERSION_PUBLIC_FIELDS),
        ("current_restriction", _SOURCE_RESTRICTION_PUBLIC_FIELDS),
        ("freshness", _SOURCE_FRESHNESS_PUBLIC_FIELDS),
    ):
        if field in source and source[field] is not None:
            source[field] = _allow_list_mapping(source[field], fields)
    if (
        "actions" in source
        and isinstance(source["actions"], Sequence)
        and not isinstance(source["actions"], (str, bytes, bytearray))
    ):
        source["actions"] = [
            _allow_list_mapping(action, _SOURCE_ACTION_PUBLIC_FIELDS)
            for action in source["actions"]
        ]
    return source


def _allow_list_source_list_payload(payload: object) -> object:
    result = _allow_list_mapping(payload, ("items", "next_cursor"))
    if not isinstance(result, dict):
        return result
    if isinstance(result.get("items"), Sequence) and not isinstance(
        result["items"], (str, bytes, bytearray)
    ):
        result["items"] = [_allow_list_source_payload(item) for item in result["items"]]
    return result


def _validated_company_response[ResponseT: _CompanyOutputModel](
    model_type: type[ResponseT],
    payload: object,
) -> ResponseT:
    if not isinstance(payload, Mapping):
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
    try:
        return model_type.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc


def _validated_source_response[ResponseT: _SourceOutputModel](
    model_type: type[ResponseT],
    payload: object,
) -> ResponseT:
    if not isinstance(payload, Mapping):
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
    try:
        return model_type.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc


class _CompanySourceApiService(Protocol):
    def search_companies(
        self,
        *,
        owner_user_id: UUID,
        query: str,
        official_domain: str | None,
        cursor: object | None,
        limit: int,
    ) -> Mapping[str, object]: ...

    def resolve_company(
        self,
        *,
        owner_user_id: UUID,
        name: str | None,
        official_identifiers: list[str] | None,
        selected_company_id: UUID | None,
        identity_evidence_refs: list[str] | None,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def get_company(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
    ) -> Mapping[str, object] | None: ...

    def register_source(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        source_type: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...

    def list_sources(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        cursor: object | None,
        limit: int,
    ) -> Mapping[str, object]: ...

    def get_source(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
    ) -> Mapping[str, object] | None: ...


def create_company_source_api(
    *,
    service: _CompanySourceApiService | None,
    authenticator: PrincipalAuthenticator | None,
    settings: ApiBoundarySettings | None,
    cursor_codec: CursorCodec | None,
) -> FastAPI:
    """Create the company-resolution HTTP boundary from owner-supplied dependencies."""

    app = FastAPI(debug=False)
    install_api_exception_handlers(app)

    require_principal = make_principal_dependency(authenticator)
    require_service = make_configured_dependency(service)
    require_cursor_codec = make_configured_dependency(cursor_codec)
    require_idempotency_key = make_idempotency_key_dependency(settings)
    principal_dependency = cast(
        AuthenticatedPrincipal,
        Depends(require_principal),
    )
    service_dependency = cast(
        _CompanySourceApiService,
        Depends(require_service),
    )
    cursor_codec_dependency = cast(
        CursorCodec,
        Depends(require_cursor_codec),
    )
    idempotency_key_dependency = cast(
        str,
        Depends(require_idempotency_key),
    )

    @app.get("/api/v1/companies/search")
    def search_companies(
        parameters: Annotated[_CompanySearchParameters, Query()],
        principal: AuthenticatedPrincipal = principal_dependency,
        configured_service: _CompanySourceApiService = service_dependency,
        configured_cursor_codec: CursorCodec = cursor_codec_dependency,
    ) -> dict[str, object]:
        page_limit = _resolve_page_limit(settings, parameters.limit)
        filter_context = {
            "q": parameters.q,
            "official_domain": parameters.official_domain,
        }
        cursor_position: object | None = None
        if parameters.cursor is not None:
            try:
                cursor_position = configured_cursor_codec.decode(
                    parameters.cursor,
                    owner_user_id=principal.user_id,
                    route_scope=_COMPANY_SEARCH_ROUTE_SCOPE,
                    filter_context=filter_context,
                    sort=_COMPANY_SEARCH_SORT,
                )
            except StarletteHTTPException as exc:
                raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        try:
            raw_result = configured_service.search_companies(
                owner_user_id=principal.user_id,
                query=parameters.q,
                official_domain=parameters.official_domain,
                cursor=cursor_position,
                limit=page_limit,
            )
        except InvalidCompanyCursor as exc:
            raise ApiProblem(ApiErrorCode.INVALID_CURSOR) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        result = _validated_company_response(_CompanySearchServiceResponse, raw_result)
        next_cursor: str | None = None
        next_position = result.next_cursor
        if next_position is not None:
            try:
                next_cursor = configured_cursor_codec.encode(
                    owner_user_id=principal.user_id,
                    route_scope=_COMPANY_SEARCH_ROUTE_SCOPE,
                    filter_context=filter_context,
                    sort=_COMPANY_SEARCH_SORT,
                    position=next_position,
                )
            except StarletteHTTPException as exc:
                raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        public_result = _CompanySearchResponse(
            candidates=result.candidates,
            selection_required=result.selection_required,
            next_cursor=next_cursor,
        )
        return cast(dict[str, object], public_result.model_dump(mode="json"))

    @app.post("/api/v1/companies/resolve")
    def resolve_company(
        body: _CompanyResolutionBody,
        principal: AuthenticatedPrincipal = principal_dependency,
        idempotency_key: str = idempotency_key_dependency,
        configured_service: _CompanySourceApiService = service_dependency,
    ) -> dict[str, object]:
        try:
            raw_result = configured_service.resolve_company(
                owner_user_id=principal.user_id,
                name=body.name,
                official_identifiers=body.official_identifiers,
                selected_company_id=body.selected_company_id,
                identity_evidence_refs=body.identity_evidence_refs,
                idempotency_key=idempotency_key,
            )
        except CompanyResolutionIdempotencyConflict as exc:
            raise ApiProblem(ApiErrorCode.IDEMPOTENCY_CONFLICT) from exc
        except CompanyResolutionIdempotencyUnavailable as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        result = _validated_company_response(_CompanyResolutionResponse, raw_result)
        return cast(dict[str, object], result.model_dump(mode="json"))

    @app.get("/api/v1/companies/{company_id}")
    def get_company(
        company_id: UUID,
        principal: AuthenticatedPrincipal = principal_dependency,
        configured_service: _CompanySourceApiService = service_dependency,
    ) -> dict[str, object]:
        try:
            result = configured_service.get_company(
                owner_user_id=principal.user_id,
                company_id=company_id,
            )
        except CompanyNotFound as exc:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        if result is None:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        public_result = _validated_company_response(_CompanyDetailServiceResponse, result)
        requested_company_id = str(company_id)
        if public_result.company.company_id != requested_company_id or any(
            requested_company_id not in {relationship.company_id, relationship.related_company_id}
            for relationship in public_result.relationships
        ):
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        return cast(dict[str, object], public_result.model_dump(mode="json"))

    @app.post("/api/v1/companies/{company_id}/sources", status_code=202)
    def register_source(
        company_id: UUID,
        body: _SourceRegistrationBody,
        principal: AuthenticatedPrincipal = principal_dependency,
        idempotency_key: str = idempotency_key_dependency,
        configured_service: _CompanySourceApiService = service_dependency,
    ) -> dict[str, object]:
        try:
            raw_result = configured_service.register_source(
                owner_user_id=principal.user_id,
                company_id=company_id,
                url=body.url,
                source_type=body.source_type.value,
                analysis_request_id=body.analysis_request_id,
                idempotency_key=idempotency_key,
            )
        except CompanyNotFound as exc:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        result = _validated_source_response(
            _SourceRegistrationResponse,
            _allow_list_mapping(raw_result, ("source_id", "job_id", "status_url")),
        )
        return cast(dict[str, object], result.model_dump(mode="json"))

    @app.get("/api/v1/companies/{company_id}/sources")
    def list_sources(
        company_id: UUID,
        parameters: Annotated[_SourceListParameters, Query()],
        principal: AuthenticatedPrincipal = principal_dependency,
        configured_service: _CompanySourceApiService = service_dependency,
        configured_cursor_codec: CursorCodec = cursor_codec_dependency,
    ) -> dict[str, object]:
        page_limit = _resolve_page_limit(settings, parameters.limit)
        filter_context = {"company_id": str(company_id)}
        cursor_position: object | None = None
        if parameters.cursor is not None:
            try:
                cursor_position = configured_cursor_codec.decode(
                    parameters.cursor,
                    owner_user_id=principal.user_id,
                    route_scope=_COMPANY_SOURCE_LIST_ROUTE_SCOPE,
                    filter_context=filter_context,
                    sort=_COMPANY_SOURCE_LIST_SORT,
                )
            except StarletteHTTPException as exc:
                raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        try:
            raw_result = configured_service.list_sources(
                owner_user_id=principal.user_id,
                company_id=company_id,
                cursor=cursor_position,
                limit=page_limit,
            )
        except CompanyNotFound as exc:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        result = _validated_source_response(
            _SourceListServiceResponse,
            _allow_list_source_list_payload(raw_result),
        )
        next_cursor: str | None = None
        if result.next_cursor is not None:
            try:
                next_cursor = configured_cursor_codec.encode(
                    owner_user_id=principal.user_id,
                    route_scope=_COMPANY_SOURCE_LIST_ROUTE_SCOPE,
                    filter_context=filter_context,
                    sort=_COMPANY_SOURCE_LIST_SORT,
                    position=result.next_cursor,
                )
            except StarletteHTTPException as exc:
                raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        public_result = _SourceListResponse(items=result.items, next_cursor=next_cursor)
        return cast(
            dict[str, object],
            public_result.model_dump(mode="json", exclude_none=True),
        )

    @app.get("/api/v1/sources/{source_id}")
    def get_source(
        source_id: UUID,
        principal: AuthenticatedPrincipal = principal_dependency,
        configured_service: _CompanySourceApiService = service_dependency,
    ) -> dict[str, object]:
        try:
            raw_result = configured_service.get_source(
                owner_user_id=principal.user_id,
                source_id=source_id,
            )
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        if raw_result is None:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        source = _validated_source_response(
            _SourceResponse,
            _allow_list_source_payload(raw_result),
        )
        public_result = _SourceDetailResponse(source=source)
        public_payload = public_result.model_dump(mode="json", exclude_none=True)
        public_source = cast(dict[str, object], public_payload["source"])
        for field in ("current_observation", "latest_available_version"):
            if getattr(source, field) is None:
                public_source[field] = None
        return cast(dict[str, object], public_payload)

    return app


class _JobPostingImportBody(_ApiModel):
    company_id: UUID
    url: str = Field(min_length=1)
    analysis_request_id: str | None = Field(default=None, min_length=1)

    @field_validator("url")
    @classmethod
    def _canonicalize_url(cls, value: str) -> str:
        try:
            return canonicalize_source_url(value)
        except ValueError as exc:
            raise ValueError("job posting URL is invalid") from exc

    @field_validator("analysis_request_id")
    @classmethod
    def _reject_blank_analysis_request_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("blank text is not allowed")
        return value


class _JobPostingSelectionParameters(_ApiModel):
    source_version_id: UUID | None = None
    extraction_revision_id: UUID | None = None


class _JobPostingImportResponse(_ApiModel):
    job_posting_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    status_url: str = Field(min_length=1)

    @field_validator("job_posting_id", "source_id", "job_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        return _canonical_uuid_text(value)

    @model_validator(mode="after")
    def _validate_status_url(self) -> _JobPostingImportResponse:
        if self.status_url != f"{_API_ROUTE_PREFIX}/jobs/{self.job_id}":
            raise ValueError("status URL must identify the accepted job")
        return self


class _JobPostingEvidenceResponse(_ApiModel):
    evidence_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class _JobPostingSectionResponse(_ApiModel):
    kind: str = Field(min_length=1)
    text: str = Field(min_length=1)
    evidence: list[_JobPostingEvidenceResponse]

    @field_validator("evidence")
    @classmethod
    def _validate_evidence_ids(
        cls,
        value: list[_JobPostingEvidenceResponse],
    ) -> list[_JobPostingEvidenceResponse]:
        evidence_ids = [item.evidence_id for item in value]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("section evidence identifiers must be unique")
        return value


class _JobPostingResponse(_ApiModel):
    job_posting_id: str = Field(min_length=1)
    company_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_version_id: str | None
    extraction_revision_id: str | None
    title: str | None
    sections: list[_JobPostingSectionResponse]

    @field_validator("job_posting_id", "company_id", "source_id")
    @classmethod
    def _validate_required_identifiers(cls, value: str) -> str:
        return _canonical_uuid_text(value)

    @field_validator("source_version_id", "extraction_revision_id")
    @classmethod
    def _validate_optional_identifiers(cls, value: str | None) -> str | None:
        return None if value is None else _canonical_uuid_text(value)


class _JobPostingDetailResponse(_ApiModel):
    job_posting: _JobPostingResponse


class _JobPostingApiService(Protocol):
    def import_job_posting(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> object: ...

    def get_job_posting(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
        source_version_id: UUID | None,
        extraction_revision_id: UUID | None,
    ) -> object: ...


def _service_field(payload: object, field: str) -> object:
    if isinstance(payload, Mapping):
        if field not in payload:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        return payload[field]
    try:
        return getattr(payload, field)
    except AttributeError as exc:
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc


def _optional_service_field(payload: object, field: str) -> object | None:
    if isinstance(payload, Mapping):
        return payload.get(field)
    return getattr(payload, field, None)


def _service_uuid_text(payload: object, field: str) -> str:
    try:
        return _canonical_uuid_text(str(_service_field(payload, field)))
    except ValueError as exc:
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc


def _optional_service_uuid_text(payload: object, field: str) -> str | None:
    value = _service_field(payload, field)
    if value is None:
        return None
    try:
        return _canonical_uuid_text(str(value))
    except ValueError as exc:
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc


def _service_sequence(payload: object) -> Sequence[object]:
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return payload
    raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)


def _validated_job_posting_response[ResponseT: _ApiModel](
    response_type: type[ResponseT],
    payload: object,
) -> ResponseT:
    try:
        return response_type.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc


def _public_mapping_job_posting(payload: object) -> dict[str, object]:
    sections = []
    for raw_section in _service_sequence(_service_field(payload, "sections")):
        evidence = [
            {
                "evidence_id": _service_field(raw_evidence, "evidence_id"),
                "excerpt": _service_field(raw_evidence, "excerpt"),
            }
            for raw_evidence in _service_sequence(_service_field(raw_section, "evidence"))
        ]
        sections.append(
            {
                "kind": _service_field(raw_section, "kind"),
                "text": _service_field(raw_section, "text"),
                "evidence": evidence,
            }
        )
    return {
        "job_posting_id": _service_uuid_text(payload, "job_posting_id"),
        "company_id": _service_uuid_text(payload, "company_id"),
        "source_id": _service_uuid_text(payload, "source_id"),
        "source_version_id": _optional_service_uuid_text(payload, "source_version_id"),
        "extraction_revision_id": _optional_service_uuid_text(payload, "extraction_revision_id"),
        "title": _service_field(payload, "title"),
        "sections": sections,
    }


def _service_view_job_posting(payload: object) -> dict[str, object]:
    if (
        isinstance(payload, JobPostingView)
        and payload.failure_reference is not None
        and (payload.source_version_id is None or payload.extraction_revision_id is None)
    ):
        # Detailed failure/status belongs to the W1 /api/v1/jobs/{job_id} boundary.
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)

    source_version_id = _service_field(payload, "source_version_id")
    evidence_by_id: dict[str, object] = {}
    for snapshot in _service_sequence(_service_field(payload, "evidence")):
        evidence_id = str(_service_field(snapshot, "evidence_id"))
        if evidence_id in evidence_by_id:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        if _service_field(snapshot, "source_version_id") != source_version_id:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        evidence_by_id[evidence_id] = snapshot

    sections = []
    for raw_section in _service_sequence(_service_field(payload, "sections")):
        evidence = []
        for raw_evidence_id in _service_sequence(_service_field(raw_section, "evidence_ids")):
            evidence_id = str(raw_evidence_id)
            snapshot = evidence_by_id.get(evidence_id)
            if snapshot is None:
                raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "excerpt": _service_field(snapshot, "text_excerpt"),
                }
            )
        sections.append(
            {
                "kind": _service_field(raw_section, "kind"),
                "text": _service_field(raw_section, "text_raw"),
                "evidence": evidence,
            }
        )
    return {
        "job_posting_id": _service_uuid_text(payload, "job_posting_id"),
        "company_id": _service_uuid_text(payload, "company_id"),
        "source_id": _service_uuid_text(payload, "source_id"),
        "source_version_id": _optional_service_uuid_text(payload, "source_version_id"),
        "extraction_revision_id": _optional_service_uuid_text(payload, "extraction_revision_id"),
        "title": _service_field(payload, "title"),
        "sections": sections,
    }


def _public_job_posting_detail(payload: object) -> dict[str, object]:
    if isinstance(payload, Mapping) and "job_posting" in payload:
        posting = _public_mapping_job_posting(payload["job_posting"])
    else:
        posting = _service_view_job_posting(payload)
    return {"job_posting": posting}


def create_job_posting_api(
    *,
    service: _JobPostingApiService | None,
    authenticator: PrincipalAuthenticator | None,
    settings: ApiBoundarySettings | None,
) -> FastAPI:
    """Create the authenticated job-posting HTTP boundary."""

    app = FastAPI(debug=False)
    install_api_exception_handlers(app)

    require_principal = make_principal_dependency(authenticator)
    require_service = make_configured_dependency(service)
    require_idempotency_key = make_idempotency_key_dependency(settings)
    principal_dependency = cast(
        AuthenticatedPrincipal,
        Depends(require_principal),
    )
    service_dependency = cast(
        _JobPostingApiService,
        Depends(require_service),
    )
    idempotency_key_dependency = cast(
        str,
        Depends(require_idempotency_key),
    )

    @app.post("/api/v1/job-postings/import", status_code=202)
    def import_job_posting(
        body: _JobPostingImportBody,
        principal: AuthenticatedPrincipal = principal_dependency,
        idempotency_key: str = idempotency_key_dependency,
        configured_service: _JobPostingApiService = service_dependency,
    ) -> dict[str, object]:
        try:
            raw_result = configured_service.import_job_posting(
                owner_user_id=principal.user_id,
                company_id=body.company_id,
                url=body.url,
                analysis_request_id=body.analysis_request_id,
                idempotency_key=idempotency_key,
            )
        except JobPostingNotFound as exc:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND) from exc
        except JobPostingSelectionInvalid as exc:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT) from exc
        except JobPostingContentUnavailable as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        job_id = _service_uuid_text(raw_result, "job_id")
        result = _validated_job_posting_response(
            _JobPostingImportResponse,
            {
                "job_posting_id": _service_uuid_text(raw_result, "job_posting_id"),
                "source_id": _service_uuid_text(raw_result, "source_id"),
                "job_id": job_id,
                "status_url": f"{_API_ROUTE_PREFIX}/jobs/{job_id}",
            },
        )
        return cast(dict[str, object], result.model_dump(mode="json"))

    @app.get("/api/v1/job-postings/{job_posting_id}")
    def get_job_posting(
        job_posting_id: UUID,
        parameters: Annotated[_JobPostingSelectionParameters, Query()],
        principal: AuthenticatedPrincipal = principal_dependency,
        configured_service: _JobPostingApiService = service_dependency,
    ) -> dict[str, object]:
        try:
            raw_result = configured_service.get_job_posting(
                owner_user_id=principal.user_id,
                job_posting_id=job_posting_id,
                source_version_id=parameters.source_version_id,
                extraction_revision_id=parameters.extraction_revision_id,
            )
        except JobPostingNotFound as exc:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND) from exc
        except JobPostingSelectionInvalid as exc:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT) from exc
        except JobPostingContentUnavailable as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        if raw_result is None:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        result = _validated_job_posting_response(
            _JobPostingDetailResponse,
            _public_job_posting_detail(raw_result),
        )
        if result.job_posting.job_posting_id != str(job_posting_id):
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        return cast(dict[str, object], result.model_dump(mode="json"))

    return app


class _SourceEvidenceListParameters(_SourceListParameters):
    extraction_revision_id: UUID


class _SourceRefreshRequest(_ApiModel):
    analysis_request_id: UUID | None = None


class _VersionEvidenceSourceVersionResponse(_ApiModel):
    source_version_id: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=1)
    source_type: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    hash_profile_version: str = Field(min_length=1)
    representation: str = Field(min_length=1)
    collected_at: datetime

    @field_validator("source_version_id")
    @classmethod
    def _validate_source_version_id(cls, value: str) -> str:
        return _canonical_uuid_text(value)


class _VersionEvidenceEvidenceResponse(_ApiModel):
    evidence_id: str = Field(min_length=1)
    source_version_id: str = Field(min_length=1)
    section_title: str | None = Field(default=None, min_length=1)
    text_excerpt: str = Field(min_length=1)
    locator: dict[str, object]
    chunk_order: int = Field(ge=0)
    origin_kind: str = Field(min_length=1)

    @field_validator("evidence_id", "source_version_id")
    @classmethod
    def _validate_identifiers(cls, value: str) -> str:
        return _canonical_uuid_text(value)


class _VersionEvidenceVersionListResponse(_ApiModel):
    items: list[_VersionEvidenceSourceVersionResponse]
    next_cursor: str | None = Field(default=None, min_length=1)
    current_restriction: _SourceRestrictionResponse | None = None


class _VersionEvidenceEvidenceListResponse(_ApiModel):
    items: list[_VersionEvidenceEvidenceResponse]
    next_cursor: str | None = Field(default=None, min_length=1)
    retention_scope: str = Field(min_length=1)
    body_ref: str | None = Field(default=None, min_length=1)
    current_restriction: _SourceRestrictionResponse | None = None


class _VersionEvidenceApiService(Protocol):
    def list_source_versions(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
        cursor: object | None,
        limit: int,
    ) -> object: ...

    def list_evidence(
        self,
        *,
        owner_user_id: UUID,
        source_version_id: UUID,
        extraction_revision_id: UUID,
        cursor: object | None,
        limit: int,
    ) -> object: ...

    def refresh_source(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
        analysis_request_id: UUID | None,
        idempotency_key: str,
    ) -> object: ...


def _validated_version_evidence_response[ResponseT: _ApiModel](
    response_type: type[ResponseT],
    payload: object,
) -> ResponseT:
    try:
        return response_type.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc


def _public_current_restriction(payload: object) -> _SourceRestrictionResponse | None:
    raw_restriction = _optional_service_field(payload, "current_restriction")
    if raw_restriction is None:
        return None
    if not isinstance(raw_restriction, Mapping) and not any(
        hasattr(raw_restriction, field) for field in _SOURCE_RESTRICTION_PUBLIC_FIELDS
    ):
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
    safe_restriction: dict[str, object] = {}
    for field in _SOURCE_RESTRICTION_PUBLIC_FIELDS:
        value = _optional_service_field(raw_restriction, field)
        if value is not None:
            safe_restriction[field] = value
    return _validated_version_evidence_response(
        _SourceRestrictionResponse,
        safe_restriction,
    )


def _public_source_version(payload: object) -> _VersionEvidenceSourceVersionResponse:
    return _validated_version_evidence_response(
        _VersionEvidenceSourceVersionResponse,
        {
            "source_version_id": _service_uuid_text(payload, "source_version_id"),
            "title": _optional_service_field(payload, "title"),
            "source_type": _service_field(payload, "source_type"),
            "canonical_url": _service_field(payload, "canonical_url"),
            "content_hash": _service_field(payload, "content_hash"),
            "hash_profile_version": _service_field(payload, "hash_profile_version"),
            "representation": _service_field(payload, "representation"),
            "collected_at": _service_field(payload, "collected_at"),
        },
    )


def _public_locator(payload: object) -> dict[str, object]:
    kind = _service_field(payload, "kind")
    value = _optional_service_field(payload, "value")
    normalization_version = _optional_service_field(payload, "normalization_version")
    start = _optional_service_field(payload, "start")
    end = _optional_service_field(payload, "end")
    has_offsets = start is not None or end is not None
    if (
        not isinstance(kind, str)
        or not kind.strip()
        or (value is not None and (not isinstance(value, str) or not value.strip()))
        or (
            normalization_version is not None
            and (not isinstance(normalization_version, str) or not normalization_version.strip())
        )
        or (
            has_offsets
            and (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 0
                or end <= start
            )
        )
        or (value is None and not has_offsets)
    ):
        raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
    public_locator: dict[str, object] = {"kind": kind}
    for field, field_value in (
        ("value", value),
        ("normalization_version", normalization_version),
        ("start", start),
        ("end", end),
    ):
        if field_value is not None:
            public_locator[field] = field_value
    return public_locator


def _public_evidence(payload: object) -> _VersionEvidenceEvidenceResponse:
    locator = _service_field(payload, "locator")
    return _validated_version_evidence_response(
        _VersionEvidenceEvidenceResponse,
        {
            "evidence_id": _service_uuid_text(payload, "evidence_id"),
            "source_version_id": _service_uuid_text(payload, "source_version_id"),
            "section_title": _optional_service_field(payload, "section_title"),
            "text_excerpt": _service_field(payload, "text_excerpt"),
            "locator": _public_locator(locator),
            "chunk_order": _service_field(payload, "chunk_order"),
            "origin_kind": _service_field(payload, "origin_kind"),
        },
    )


def create_version_evidence_api(
    *,
    service: _VersionEvidenceApiService | None,
    authenticator: PrincipalAuthenticator | None,
    settings: ApiBoundarySettings | None,
    cursor_codec: CursorCodec | None,
) -> FastAPI:
    """Create the owner-authenticated SourceVersion, Evidence, and refresh boundary."""

    app = FastAPI(debug=False)
    install_api_exception_handlers(app)

    require_principal = make_principal_dependency(authenticator)
    require_service = make_configured_dependency(service)
    require_cursor_codec = make_configured_dependency(cursor_codec)
    require_idempotency_key = make_idempotency_key_dependency(settings)

    def require_refresh_context(request: Request) -> tuple[AuthenticatedPrincipal, str]:
        return require_principal(request), require_idempotency_key(request)

    principal_dependency = cast(
        AuthenticatedPrincipal,
        Depends(require_principal),
    )
    service_dependency = cast(
        _VersionEvidenceApiService,
        Depends(require_service),
    )
    cursor_codec_dependency = cast(
        CursorCodec,
        Depends(require_cursor_codec),
    )
    refresh_context_dependency = cast(
        tuple[AuthenticatedPrincipal, str],
        Depends(require_refresh_context),
    )

    @app.get("/api/v1/sources/{source_id}/versions")
    def list_source_versions(
        source_id: UUID,
        parameters: Annotated[_SourceListParameters, Query()],
        principal: AuthenticatedPrincipal = principal_dependency,
        configured_service: _VersionEvidenceApiService = service_dependency,
        configured_cursor_codec: CursorCodec = cursor_codec_dependency,
    ) -> dict[str, object]:
        page_limit = _resolve_page_limit(settings, parameters.limit)
        filter_context = {"source_id": str(source_id)}
        cursor_position: object | None = None
        if parameters.cursor is not None:
            try:
                cursor_position = configured_cursor_codec.decode(
                    parameters.cursor,
                    owner_user_id=principal.user_id,
                    route_scope=_SOURCE_VERSION_LIST_ROUTE_SCOPE,
                    filter_context=filter_context,
                    sort=_SOURCE_VERSION_LIST_SORT,
                )
            except StarletteHTTPException as exc:
                raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        try:
            raw_result = configured_service.list_source_versions(
                owner_user_id=principal.user_id,
                source_id=source_id,
                cursor=cursor_position,
                limit=page_limit,
            )
        except SourceVersionEvidenceNotFound as exc:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND) from exc
        except SourceVersionEvidenceInvalidInput as exc:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT) from exc
        except SourceVersionEvidenceDependencyUnavailable as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc

        items = [
            _public_source_version(item)
            for item in _service_sequence(_service_field(raw_result, "items"))
        ]
        next_position = _service_field(raw_result, "next_cursor")
        next_cursor: str | None = None
        if next_position is not None:
            try:
                next_cursor = configured_cursor_codec.encode(
                    owner_user_id=principal.user_id,
                    route_scope=_SOURCE_VERSION_LIST_ROUTE_SCOPE,
                    filter_context=filter_context,
                    sort=_SOURCE_VERSION_LIST_SORT,
                    position=next_position,
                )
            except StarletteHTTPException as exc:
                raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        public_result = _VersionEvidenceVersionListResponse(
            items=items,
            next_cursor=next_cursor,
            current_restriction=_public_current_restriction(raw_result),
        )
        return cast(
            dict[str, object],
            public_result.model_dump(mode="json"),
        )

    @app.get("/api/v1/source-versions/{source_version_id}/evidence")
    def list_evidence(
        source_version_id: UUID,
        parameters: Annotated[_SourceEvidenceListParameters, Query()],
        principal: AuthenticatedPrincipal = principal_dependency,
        configured_service: _VersionEvidenceApiService = service_dependency,
        configured_cursor_codec: CursorCodec = cursor_codec_dependency,
    ) -> dict[str, object]:
        page_limit = _resolve_page_limit(settings, parameters.limit)
        filter_context = {
            "source_version_id": str(source_version_id),
            "extraction_revision_id": str(parameters.extraction_revision_id),
        }
        cursor_position: object | None = None
        if parameters.cursor is not None:
            try:
                cursor_position = configured_cursor_codec.decode(
                    parameters.cursor,
                    owner_user_id=principal.user_id,
                    route_scope=_SOURCE_EVIDENCE_LIST_ROUTE_SCOPE,
                    filter_context=filter_context,
                    sort=_SOURCE_EVIDENCE_LIST_SORT,
                )
            except StarletteHTTPException as exc:
                raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        try:
            raw_result = configured_service.list_evidence(
                owner_user_id=principal.user_id,
                source_version_id=source_version_id,
                extraction_revision_id=parameters.extraction_revision_id,
                cursor=cursor_position,
                limit=page_limit,
            )
        except SourceVersionEvidenceNotFound as exc:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND) from exc
        except SourceVersionEvidenceInvalidInput as exc:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT) from exc
        except SourceVersionEvidenceDependencyUnavailable as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc

        items = [
            _public_evidence(item)
            for item in _service_sequence(_service_field(raw_result, "items"))
        ]
        if any(item.source_version_id != str(source_version_id) for item in items):
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        next_position = _service_field(raw_result, "next_cursor")
        next_cursor: str | None = None
        if next_position is not None:
            try:
                next_cursor = configured_cursor_codec.encode(
                    owner_user_id=principal.user_id,
                    route_scope=_SOURCE_EVIDENCE_LIST_ROUTE_SCOPE,
                    filter_context=filter_context,
                    sort=_SOURCE_EVIDENCE_LIST_SORT,
                    position=next_position,
                )
            except StarletteHTTPException as exc:
                raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        raw_body_ref = _service_field(raw_result, "body_ref")
        try:
            body_ref = (
                None
                if raw_body_ref is None
                else RetainedBodyReference(body_ref=cast(str, raw_body_ref)).body_ref
            )
        except ValueError as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        retention_scope = _service_field(raw_result, "retention_scope")
        if not isinstance(retention_scope, str):
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        public_result = _VersionEvidenceEvidenceListResponse(
            items=items,
            next_cursor=next_cursor,
            retention_scope=retention_scope,
            body_ref=body_ref,
            current_restriction=_public_current_restriction(raw_result),
        )
        return cast(
            dict[str, object],
            public_result.model_dump(mode="json"),
        )

    @app.post("/api/v1/sources/{source_id}/refresh", status_code=202)
    async def refresh_source(
        source_id: UUID,
        request: Request,
        refresh_context: tuple[AuthenticatedPrincipal, str] = refresh_context_dependency,
        configured_service: _VersionEvidenceApiService = service_dependency,
    ) -> dict[str, object]:
        principal, idempotency_key = refresh_context
        try:
            body = _SourceRefreshRequest.model_validate(await request.json())
        except (TypeError, ValueError) as exc:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT) from exc
        try:
            raw_result = configured_service.refresh_source(
                owner_user_id=principal.user_id,
                source_id=source_id,
                analysis_request_id=body.analysis_request_id,
                idempotency_key=idempotency_key,
            )
        except SourceVersionEvidenceNotFound as exc:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND) from exc
        except SourceVersionEvidenceInvalidInput as exc:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT) from exc
        except SourceRefreshIdempotencyConflict as exc:
            raise ApiProblem(ApiErrorCode.IDEMPOTENCY_CONFLICT) from exc
        except SourceVersionEvidenceDependencyUnavailable as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc
        except StarletteHTTPException as exc:
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE) from exc

        response_source_id = _service_uuid_text(raw_result, "source_id")
        if response_source_id != str(source_id):
            raise ApiProblem(ApiErrorCode.DEPENDENCY_UNAVAILABLE)
        response_job_id = _service_uuid_text(raw_result, "job_id")
        public_result = _SourceRegistrationResponse(
            source_id=response_source_id,
            job_id=response_job_id,
            status_url=f"{_API_ROUTE_PREFIX}/jobs/{response_job_id}",
        )
        return cast(
            dict[str, object],
            public_result.model_dump(mode="json"),
        )

    return app
