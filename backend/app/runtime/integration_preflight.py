"""Fail-closed local startup checks for the full-chain integration profile."""

from __future__ import annotations

import hashlib
import re
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import Settings

CONTRACT_NAMES = frozenset(
    {
        "public-collection-api.openapi.yaml",
        "w1-w3-analysis-plan-command.schema.json",
        "deployment-manifest.schema.json",
    }
)
CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts" / "integration" / "v1"
_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


class IntegrationPreflightError(ValueError):
    """Non-sensitive startup rejection for invalid integration configuration."""


def validate_integration_startup(
    settings: Settings,
    *,
    contract_root: Path = CONTRACT_ROOT,
) -> dict[str, str]:
    if settings.integration_real_data_enabled:
        raise IntegrationPreflightError("REAL mode requires an approved policy manifest")
    if not settings.integration_enabled:
        return {"status": "disabled"}

    if not settings.integration_policy_revision or settings.integration_policy_revision.startswith(
        "SET_"
    ):
        raise IntegrationPreflightError("integration policy revision is missing or mutable")
    for origin in (
        settings.integration_w2_private_origin,
        settings.integration_w3_private_origin,
        settings.integration_w4_private_origin,
    ):
        _validate_private_origin(origin)

    pins = settings.integration_contract_pins
    if set(pins) != CONTRACT_NAMES:
        raise IntegrationPreflightError("integration contract pins are incomplete")
    for name, expected in pins.items():
        if not _SHA256.fullmatch(expected):
            raise IntegrationPreflightError("integration contract pins are mutable or malformed")
        path = contract_root / name
        try:
            normalized = path.read_bytes().replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"
        except OSError as error:
            raise IntegrationPreflightError("integration contract is unavailable") from error
        actual = hashlib.sha256(normalized).hexdigest()
        if expected.removeprefix("sha256:") != actual:
            raise IntegrationPreflightError("integration contract drift detected")
    return {"status": "ready", "policy_revision": settings.integration_policy_revision}


def _validate_private_origin(origin: str | None) -> None:
    if not origin:
        raise IntegrationPreflightError("private integration origin is missing")
    parsed = urlsplit(origin)
    host = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise IntegrationPreflightError("private integration origin is invalid")
    try:
        private_ip = ip_address(host).is_private
    except ValueError:
        private_ip = False
    private_dns = host == "localhost" or "." not in host or host.endswith(
        (".internal", ".local")
    )
    if not (private_ip or private_dns):
        raise IntegrationPreflightError("integration origin must be private")
