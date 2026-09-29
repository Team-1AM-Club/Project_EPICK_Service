"""Protected HTTPS caller for the revision-pinned W1 command lookup.

The lookup is read-only currentness data.  It is not a worker permit, slot, lease,
or execution authorization, and this module never retries a request automatically.
"""

from __future__ import annotations

import http.client
import json
import math
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from epick_engine.source_collection.w1_transport import (
    LookupRequest,
    LookupResponse,
    W1CommandDispatch,
    W1DirectSourceRegistrationDispatch,
    W1Dispatch,
    W1WireContractError,
    _revalidate_model,
    decode_lookup_response,
    validate_dispatch_lookup,
)

LOOKUP_TARGET = "/internal/v1/job-commands/lookup"
SOURCE_ONBOARDING_TARGET = "/internal/v1/w2-private/source-onboarding-lookup"
MAX_LOOKUP_REQUEST_BYTES = 16 * 1024
MAX_LOOKUP_RESPONSE_BYTES = 64 * 1024
DEFAULT_LOOKUP_TIMEOUT_SECONDS = 5.0
_MAX_BEARER_BYTES = 8 * 1024


class W1LookupClientError(RuntimeError):
    """Sanitized local/HTTP transport failure without endpoint or private data."""

    def __init__(self, code: str) -> None:
        super().__init__(f"W1 protected lookup client failed: {code}")
        self.code = code


@dataclass(frozen=True, slots=True)
class LookupHTTPResponse:
    """Bounded response returned by a lookup transport seam."""

    status: int
    content_type: str | None
    body: bytes = field(repr=False)


class SourceOnboardingMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_id: UUID
    company_id: UUID
    canonical_url: str
    source_type: str
    title: str | None
    company_legal_name: str
    company_official_domain: str | None
    company_identification_status: str


class SourceOnboardingLookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal["w1.private.source-onboarding-lookup.v1"]
    command_id: UUID
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    source: SourceOnboardingMetadata | None

    @model_validator(mode="after")
    def validate_binding(self) -> SourceOnboardingLookupResponse:
        if (self.status == "AVAILABLE" and self.source is None) or (
            self.status == "UNAVAILABLE" and self.source is not None
        ):
            raise ValueError("invalid source onboarding result")
        return self


class LookupHTTPTransport(Protocol):
    """Minimal injectable seam; implementations must perform exactly one HTTPS POST."""

    def post(
        self,
        *,
        host: str,
        port: int,
        target: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
        ssl_context: ssl.SSLContext,
        max_response_bytes: int,
    ) -> LookupHTTPResponse: ...


@dataclass(frozen=True, slots=True, repr=False)
class _LookupEndpoint:
    host: str
    port: int


class _StdlibHTTPSLookupTransport:
    """Direct stdlib HTTPS transport with no proxy lookup, redirects, or retries."""

    def post(
        self,
        *,
        host: str,
        port: int,
        target: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
        ssl_context: ssl.SSLContext,
        max_response_bytes: int,
    ) -> LookupHTTPResponse:
        connection = http.client.HTTPSConnection(
            host,
            port,
            timeout=timeout,
            context=ssl_context,
        )
        try:
            connection.request("POST", target, body=body, headers=dict(headers))
            response = connection.getresponse()
            response_body = response.read(max_response_bytes + 1)
            return LookupHTTPResponse(
                status=response.status,
                content_type=response.getheader("Content-Type"),
                body=response_body,
            )
        finally:
            connection.close()


def _configuration_error() -> W1LookupClientError:
    return W1LookupClientError("INVALID_CONFIGURATION")


def validate_lookup_endpoint(value: object) -> _LookupEndpoint:
    """Validate and parse the one permitted W1 protected lookup endpoint."""

    if not isinstance(value, str) or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in value
    ):
        raise _configuration_error()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        raise _configuration_error() from None
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != LOOKUP_TARGET
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise _configuration_error()
    return _LookupEndpoint(host=host, port=443 if port is None else port)


def _validate_bearer(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
        or len(value) > _MAX_BEARER_BYTES
    ):
        raise _configuration_error()
    return value


def _validate_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _configuration_error()
    timeout: float | None = None
    conversion_failed = False
    try:
        timeout = float(value)
    except OverflowError:
        conversion_failed = True
    if conversion_failed or timeout is None:
        raise _configuration_error() from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise _configuration_error()
    return timeout


def _require_verified_tls(context: object) -> ssl.SSLContext:
    if (
        not isinstance(context, ssl.SSLContext)
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.check_hostname is not True
    ):
        raise _configuration_error()
    return context


def _revalidate_dispatch(dispatch: object) -> W1Dispatch:
    if isinstance(dispatch, W1CommandDispatch):
        return _revalidate_model(dispatch, W1CommandDispatch, label="W1 command dispatch")
    if isinstance(dispatch, W1DirectSourceRegistrationDispatch):
        return _revalidate_model(
            dispatch,
            W1DirectSourceRegistrationDispatch,
            label="W1 direct registration dispatch",
        )
    raise W1WireContractError("invalid W1 dispatch lookup context")


def _json_content_type_supported(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = [part.strip() for part in value.split(";")]
    if not parts or parts[0].lower() != "application/json":
        return False
    for parameter in parts[1:]:
        if "=" not in parameter:
            return False
        name, raw_value = parameter.split("=", 1)
        charset = raw_value.strip().strip('"').lower()
        if name.strip().lower() != "charset" or charset != "utf-8":
            return False
    return True


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object member")
        result[key] = value
    return result


def _reject_nonfinite_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _decode_strict_json(body: bytes) -> object:
    try:
        text = body.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_constant,
            parse_float=_parse_finite_float,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise W1LookupClientError("INVALID_RESPONSE_BODY") from None


def _post_bounded_json(
    *,
    endpoint: _LookupEndpoint,
    bearer: str,
    ssl_context: ssl.SSLContext,
    transport: LookupHTTPTransport,
    target: str,
    request_payload: object,
    timeout: float,
) -> tuple[int, object]:
    """Perform one direct HTTPS POST and return only bounded, strict JSON."""

    body: bytes | None = None
    request_encoding_failed = False
    try:
        body = json.dumps(
            request_payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        request_encoding_failed = True
    if request_encoding_failed or body is None:
        raise W1LookupClientError("INVALID_REQUEST_BODY") from None
    if len(body) > MAX_LOOKUP_REQUEST_BYTES:
        raise W1LookupClientError("REQUEST_TOO_LARGE")

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {bearer}",
        "Content-Length": str(len(body)),
        "Content-Type": "application/json",
        "X-EPICK-Service-Principal": "w2",
    }
    context = _require_verified_tls(ssl_context)
    response: object | None = None
    transport_failed = False
    try:
        response = transport.post(
            host=endpoint.host,
            port=endpoint.port,
            target=target,
            headers=headers,
            body=body,
            timeout=timeout,
            ssl_context=context,
            max_response_bytes=MAX_LOOKUP_RESPONSE_BYTES,
        )
    except Exception:
        transport_failed = True
    if transport_failed:
        raise W1LookupClientError("TRANSPORT_FAILURE") from None

    if not isinstance(response, LookupHTTPResponse):
        raise W1LookupClientError("INVALID_TRANSPORT_RESPONSE")
    if (
        type(response.status) is not int
        or not 100 <= response.status <= 599
        or not isinstance(response.body, bytes)
        or not isinstance(response.content_type, str | type(None))
    ):
        raise W1LookupClientError("INVALID_TRANSPORT_RESPONSE")
    if len(response.body) > MAX_LOOKUP_RESPONSE_BYTES:
        raise W1LookupClientError("RESPONSE_TOO_LARGE")
    if not _json_content_type_supported(response.content_type):
        raise W1LookupClientError("UNSUPPORTED_RESPONSE_CONTENT")

    decoded: object | None = None
    decode_error: str | None = None
    try:
        decoded = _decode_strict_json(response.body)
    except W1LookupClientError as error:
        decode_error = error.code
    if decode_error is not None:
        raise W1LookupClientError(decode_error) from None
    return response.status, decoded


class W1LookupClient:
    """One-shot protected lookup client backed by W1's strict wire codecs."""

    __slots__ = ("_bearer", "_endpoint", "_ssl_context", "_timeout", "_transport")

    def __init__(
        self,
        *,
        endpoint: str,
        bearer: str,
        timeout_seconds: float = DEFAULT_LOOKUP_TIMEOUT_SECONDS,
        ssl_context: ssl.SSLContext | None = None,
        transport: LookupHTTPTransport | None = None,
    ) -> None:
        self._endpoint = validate_lookup_endpoint(endpoint)
        self._bearer = _validate_bearer(bearer)
        self._timeout = _validate_timeout(timeout_seconds)
        self._ssl_context = _require_verified_tls(
            ssl.create_default_context() if ssl_context is None else ssl_context
        )
        self._transport = _StdlibHTTPSLookupTransport() if transport is None else transport

    def __repr__(self) -> str:
        return "W1LookupClient(endpoint=<redacted>, bearer=<redacted>)"

    def lookup(self, request: LookupRequest) -> LookupResponse:
        """Perform one protected lookup; semantic availability remains read-only data."""

        if not isinstance(request, LookupRequest):
            raise W1WireContractError("invalid W1 lookup request")
        validated_request = _revalidate_model(request, LookupRequest, label="W1 lookup request")
        http_status, payload = _post_bounded_json(
            endpoint=self._endpoint,
            bearer=self._bearer,
            ssl_context=self._ssl_context,
            transport=self._transport,
            target=LOOKUP_TARGET,
            request_payload=validated_request.model_dump(mode="json", warnings="error"),
            timeout=self._timeout,
        )
        return decode_lookup_response(
            payload,
            http_status=http_status,
            request=validated_request,
        )

    def lookup_dispatch(self, dispatch: W1Dispatch) -> LookupResponse:
        """Revalidate a full dispatch before I/O, then bind the complete lookup result."""

        validated_dispatch = _revalidate_dispatch(dispatch)
        response = self.lookup(validated_dispatch.lookup_request)
        validate_dispatch_lookup(validated_dispatch, response)
        return response

    def lookup_source_onboarding(
        self, request: LookupRequest
    ) -> SourceOnboardingLookupResponse:
        """Read W1's exact Source identity for an already issued, current command.

        This is not collection approval. W2 must match its own approved site
        profile and policy before registering or fetching the Source.
        """

        if not isinstance(request, LookupRequest):
            raise W1WireContractError("invalid W1 source onboarding request")
        validated = _revalidate_model(request, LookupRequest, label="W1 lookup request")
        status, payload = _post_bounded_json(
            endpoint=self._endpoint,
            bearer=self._bearer,
            ssl_context=self._ssl_context,
            transport=self._transport,
            target=SOURCE_ONBOARDING_TARGET,
            request_payload=validated.model_dump(mode="json", warnings="error"),
            timeout=self._timeout,
        )
        if status != 200:
            raise W1LookupClientError("SOURCE_ONBOARDING_HTTP_FAILURE")
        try:
            response = SourceOnboardingLookupResponse.model_validate_json(
                json.dumps(payload, allow_nan=False)
            )
        except ValidationError:
            raise W1LookupClientError("INVALID_SOURCE_ONBOARDING_RESPONSE") from None
        if response.command_id != validated.command_id:
            raise W1LookupClientError("SOURCE_ONBOARDING_COMMAND_MISMATCH")
        return response
