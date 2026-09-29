"""Fail-closed policy and destination checks for source collection."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address, IPv6Address, ip_address
from urllib.parse import unquote, unquote_plus, urljoin, urlsplit, urlunsplit

from epick_engine.source_collection.contracts import AccessClass, OfficialStatus, Permission

type IPAddress = IPv4Address | IPv6Address
type Resolver = Callable[[str], Iterable[str]]


class PolicyBlocked(ValueError):
    """Raised when a source-policy decision does not authorize an operation."""


class UnsafeDestination(ValueError):
    """Raised when a URL or connection destination is unsafe."""


class ExecutionPolicyUnconfigured(ValueError):
    """Raised when externally supplied execution limits are incomplete or invalid."""


class UnsupportedFormat(ValueError):
    """Raised when a source representation is outside the supported MVP formats."""


class PolicyOperation(StrEnum):
    FETCH = "fetch"
    STORE_EXCERPT = "store_excerpt"
    STORE_BODY = "store_body"
    REDISTRIBUTE = "redistribute"


class Representation(StrEnum):
    HTML = "html"
    JSON = "json"


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    official_status: OfficialStatus
    access_class: AccessClass
    collection_permission: Permission
    excerpt_storage_permission: Permission
    body_storage_permission: Permission
    redistribution_permission: Permission
    revision: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision <= 0
        ):
            raise ValueError("policy revision must be a positive integer")


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    site_concurrency: int
    global_concurrency: int
    source_ttl_seconds: int
    max_response_bytes: int
    max_decompressed_bytes: int
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_redirects: int
    general_retry_limit: int
    retention_days: int


@dataclass(frozen=True, slots=True)
class ValidatedTarget:
    url: str
    hostname: str
    port: int
    resolved_addresses: frozenset[str]


@dataclass(frozen=True, slots=True)
class UntrustedDocument:
    """Document text that must never be interpreted as policy or instructions."""

    text: str


_OPERATION_PERMISSION_FIELD = {
    PolicyOperation.FETCH: "collection_permission",
    PolicyOperation.STORE_EXCERPT: "excerpt_storage_permission",
    PolicyOperation.STORE_BODY: "body_storage_permission",
    PolicyOperation.REDISTRIBUTE: "redistribution_permission",
}

_POSITIVE_INTEGER_LIMIT_FIELDS = frozenset(
    {
        "site_concurrency",
        "global_concurrency",
        "source_ttl_seconds",
        "max_response_bytes",
        "max_decompressed_bytes",
        "max_redirects",
        "retention_days",
    }
)
_NONNEGATIVE_LIMIT_FIELDS = frozenset({"general_retry_limit"})
_INTEGER_LIMIT_FIELDS = _POSITIVE_INTEGER_LIMIT_FIELDS | _NONNEGATIVE_LIMIT_FIELDS
_FLOAT_LIMIT_FIELDS = frozenset({"connect_timeout_seconds", "read_timeout_seconds"})
_EXECUTION_LIMIT_FIELDS = _INTEGER_LIMIT_FIELDS | _FLOAT_LIMIT_FIELDS
_ALLOWED_QUERY_NAMES = frozenset({"id"})


def authorize_operation(policy: PolicySnapshot, operation: PolicyOperation) -> None:
    """Authorize one operation without inferring permission from another axis."""

    if policy.official_status is not OfficialStatus.VERIFIED:
        raise PolicyBlocked("official_status does not authorize this operation")
    if policy.access_class is not AccessClass.PUBLIC:
        raise PolicyBlocked("access_class does not authorize this operation")

    permission_field = _OPERATION_PERMISSION_FIELD.get(operation)
    if permission_field is None:
        raise PolicyBlocked("unknown policy operation")
    if getattr(policy, permission_field) is not Permission.ALLOWED:
        raise PolicyBlocked(f"{permission_field} does not authorize this operation")


def authorize_robots_access(permission: Permission) -> None:
    """Authorize the path-specific robots decision independently of policy axes."""

    if permission is not Permission.ALLOWED:
        raise PolicyBlocked("robots permission does not authorize fetch")


def parse_execution_limits(values: Mapping[str, object]) -> ExecutionLimits:
    """Parse a complete set of finite, positive external-execution limits."""

    supplied_fields = set(values)
    missing_fields = _EXECUTION_LIMIT_FIELDS - supplied_fields
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ExecutionPolicyUnconfigured(f"missing execution limit: {missing}")

    unknown_fields = supplied_fields - _EXECUTION_LIMIT_FIELDS
    if unknown_fields:
        unknown = ", ".join(sorted(unknown_fields))
        raise ExecutionPolicyUnconfigured(f"unknown execution limit: {unknown}")

    parsed: dict[str, int | float] = {}
    for field in _POSITIVE_INTEGER_LIMIT_FIELDS:
        value = values[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ExecutionPolicyUnconfigured(f"{field} must be a positive integer")
        parsed[field] = value

    for field in _NONNEGATIVE_LIMIT_FIELDS:
        value = values[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ExecutionPolicyUnconfigured(f"{field} must be a non-negative integer")
        parsed[field] = value

    for field in _FLOAT_LIMIT_FIELDS:
        value = values[field]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ExecutionPolicyUnconfigured(f"{field} must be a positive finite number")
        float_value = float(value)
        if not math.isfinite(float_value) or float_value <= 0:
            raise ExecutionPolicyUnconfigured(f"{field} must be a positive finite number")
        parsed[field] = float_value

    return ExecutionLimits(
        site_concurrency=int(parsed["site_concurrency"]),
        global_concurrency=int(parsed["global_concurrency"]),
        source_ttl_seconds=int(parsed["source_ttl_seconds"]),
        max_response_bytes=int(parsed["max_response_bytes"]),
        max_decompressed_bytes=int(parsed["max_decompressed_bytes"]),
        connect_timeout_seconds=float(parsed["connect_timeout_seconds"]),
        read_timeout_seconds=float(parsed["read_timeout_seconds"]),
        max_redirects=int(parsed["max_redirects"]),
        general_retry_limit=int(parsed["general_retry_limit"]),
        retention_days=int(parsed["retention_days"]),
    )


def validate_url(url: str, resolver: Resolver) -> ValidatedTarget:
    """Validate an HTTPS URL and every address returned for its hostname."""

    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise UnsafeDestination("invalid URL") from exc

    if parts.scheme.lower() != "https" or hostname is None:
        raise UnsafeDestination("only absolute HTTPS URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise UnsafeDestination("URL credentials are not allowed")
    if _parse_ip(hostname) is not None:
        raise UnsafeDestination("literal IP destinations are not allowed")

    _validate_query(parts.query)
    try:
        normalized_hostname = hostname.encode("idna").decode("ascii").rstrip(".").lower()
    except UnicodeError as exc:
        raise UnsafeDestination("URL hostname is not valid IDNA") from exc
    if not normalized_hostname:
        raise UnsafeDestination("URL hostname is required")

    try:
        resolved = tuple(resolver(normalized_hostname))
    except Exception as exc:
        raise UnsafeDestination("destination DNS resolution failed") from exc
    if not resolved:
        raise UnsafeDestination("destination DNS returned no addresses")

    validated_addresses: set[str] = set()
    for raw_address in resolved:
        address = _require_public_ip(raw_address)
        validated_addresses.add(str(address))

    effective_port = port if port is not None else 443
    if effective_port <= 0:
        raise UnsafeDestination("URL port must be positive")
    normalized_netloc = (
        normalized_hostname if effective_port == 443 else f"{normalized_hostname}:{effective_port}"
    )
    normalized_url = urlunsplit(("https", normalized_netloc, parts.path, parts.query, ""))
    return ValidatedTarget(
        url=normalized_url,
        hostname=normalized_hostname,
        port=effective_port,
        resolved_addresses=frozenset(validated_addresses),
    )


def validate_redirect(base_url: str, location: str, resolver: Resolver) -> ValidatedTarget:
    """Resolve and fully revalidate one redirect target."""

    return validate_url(urljoin(base_url, location), resolver)


def validate_connection_destination(target: ValidatedTarget, peer_address: str) -> None:
    """Require the connected peer to remain public and match validated DNS."""

    peer = _require_public_ip(peer_address)
    if str(peer) not in target.resolved_addresses:
        raise UnsafeDestination("connection destination does not match validated DNS")


def validate_representation(url: str, content_type: str) -> Representation:
    """Accept supported HTML and official JSON while rejecting PDF/OCR inputs."""

    path = unquote(urlsplit(url).path).lower()
    media_type = content_type.partition(";")[0].strip().lower()
    if path.endswith(".pdf") or media_type in {"application/pdf", "application/x-pdf"}:
        raise UnsupportedFormat("PDF extraction, preservation, and OCR are unsupported")
    if media_type in {"text/html", "application/xhtml+xml"}:
        return Representation.HTML
    if media_type == "application/json" or media_type.endswith("+json"):
        return Representation.JSON
    raise UnsupportedFormat(f"unsupported representation: {media_type or 'unknown'}")


def _validate_query(query: str) -> None:
    if not query:
        return
    if ";" in query or "%3b" in query.casefold():
        raise UnsafeDestination("ambiguous URL query is not allowed")

    names: set[str] = set()
    for component in query.split("&"):
        if not component or "=" not in component:
            raise UnsafeDestination("ambiguous URL query is not allowed")
        raw_name, _separator, _uninspected_value = component.partition("=")
        if not raw_name or _has_invalid_percent_escape(raw_name):
            raise UnsafeDestination("invalid URL query key")

        try:
            decoded_name = unquote_plus(raw_name, encoding="utf-8", errors="strict")
        except UnicodeError as exc:
            raise UnsafeDestination("invalid URL query key") from exc
        normalized_name = decoded_name.casefold()
        if decoded_name != decoded_name.strip() or not normalized_name:
            raise UnsafeDestination("invalid URL query key")
        if normalized_name in names:
            raise UnsafeDestination("duplicate URL query key is not allowed")
        if normalized_name not in _ALLOWED_QUERY_NAMES:
            raise UnsafeDestination("unapproved credential query key is not allowed")
        names.add(normalized_name)


def _has_invalid_percent_escape(value: str) -> bool:
    hexadecimal = frozenset("0123456789abcdefABCDEF")
    index = 0
    while index < len(value):
        if value[index] != "%":
            index += 1
            continue
        if index + 2 >= len(value) or any(
            character not in hexadecimal for character in value[index + 1 : index + 3]
        ):
            return True
        index += 3
    return False


def _parse_ip(value: str) -> IPAddress | None:
    try:
        return ip_address(value)
    except ValueError:
        return None


def _require_public_ip(value: str) -> IPAddress:
    address = _parse_ip(value)
    if address is None:
        raise UnsafeDestination("DNS returned an invalid address")
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise UnsafeDestination("non-public destination is not allowed")
    return address
