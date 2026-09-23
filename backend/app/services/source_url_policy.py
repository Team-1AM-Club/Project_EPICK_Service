from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import SplitResult, urlsplit, urlunsplit


class SourceUrlPolicyError(ValueError):
    """A public URL is malformed or uses a transport W1 does not permit."""


class SourceUrlDomainMismatchError(SourceUrlPolicyError):
    """The URL is not governed by the selected Company's official domain."""


@dataclass(frozen=True)
class CanonicalSourceUrl:
    url: str
    digest: str
    normalization_version: str = "w1-official-url-v1"


def canonicalize_official_url(raw_url: str, *, official_domain: str | None) -> CanonicalSourceUrl:
    value = raw_url.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise SourceUrlPolicyError("UNSUPPORTED_URL") from error
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 80, 443}
        or (parsed.scheme.lower() == "http" and port == 443)
        or (parsed.scheme.lower() == "https" and port == 80)
    ):
        raise SourceUrlPolicyError("UNSUPPORTED_URL")

    host = _ascii_host(parsed.hostname)
    authority = _ascii_host(official_domain or "")
    if not authority or not (host == authority or host.endswith(f".{authority}")):
        raise SourceUrlDomainMismatchError("COMPANY_DOMAIN_MISMATCH")

    scheme = parsed.scheme.lower()
    netloc = host if port is None or port in {80, 443} else f"{host}:{port}"
    path = parsed.path or "/"
    canonical = urlunsplit(SplitResult(scheme, netloc, path, parsed.query, ""))
    return CanonicalSourceUrl(url=canonical, digest=sha256(canonical.encode("utf-8")).hexdigest())


def _ascii_host(value: str) -> str:
    candidate = value.strip().rstrip(".").lower()
    if not candidate:
        return ""
    try:
        return candidate.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise SourceUrlPolicyError("UNSUPPORTED_URL") from error
