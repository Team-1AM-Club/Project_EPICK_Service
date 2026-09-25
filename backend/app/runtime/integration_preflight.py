"""Fail-closed local startup checks for the full-chain integration profile."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import Connection

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
_OCI_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
W2_V2_CONTRACT_ROOT = Path(__file__).resolve().parents[2] / "contracts" / "w2" / "v2"
_W2_V2_MANIFEST = W2_V2_CONTRACT_ROOT / "private-deletion-manifest.json"
_W2_V2_PROOF_KEYS = frozenset(
    {
        "schema_version",
        "w2_source_sha",
        "w2_migration_head",
        "w2_image_digest",
        "command_schema_sha256",
        "ack_schema_sha256",
        "checked_at",
    }
)


class IntegrationPreflightError(ValueError):
    """Non-sensitive startup rejection for invalid integration configuration."""


def _w2_v2_manifest() -> dict[str, str]:
    try:
        manifest = json.loads(_W2_V2_MANIFEST.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError
        if manifest.get("w2_required_migration_head") != "0010_private_deletion_scope_v2":
            raise ValueError
        source_sha = manifest.get("w2_source_sha")
        if not isinstance(source_sha, str) or re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
            raise ValueError
        for kind in ("command", "ack"):
            expected_name = f"private-deletion-{kind}-v2.schema.json"
            if manifest.get(f"{kind}_schema_path") != expected_name:
                raise ValueError
            actual = hashlib.sha256((W2_V2_CONTRACT_ROOT / expected_name).read_bytes()).hexdigest()
            if manifest.get(f"{kind}_schema_sha256") != actual:
                raise ValueError
    except (OSError, ValueError, TypeError) as error:
        raise IntegrationPreflightError("W2 deletion contract pin is invalid") from error
    return manifest


def create_w2_deletion_activation_proof(
    connection: Connection,
    *,
    observed_source_sha: str,
    image_digest: str,
    checked_at: datetime,
) -> dict[str, str]:
    """Bind an actual read-only W2 revision check to the adopted v2 contract."""
    manifest = _w2_v2_manifest()
    if observed_source_sha != manifest["w2_source_sha"] or not _OCI_DIGEST.fullmatch(image_digest):
        raise IntegrationPreflightError("W2 deletion source/image pin is invalid")
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise IntegrationPreflightError("W2 deletion preflight time is invalid")
    try:
        revisions = tuple(connection.scalars(text("SELECT version_num FROM alembic_version")).all())
    except Exception as error:
        raise IntegrationPreflightError("W2 deletion database head is unavailable") from error
    if revisions != (manifest["w2_required_migration_head"],):
        raise IntegrationPreflightError("W2 deletion database head is not approved")
    return {
        "schema_version": "w1.w2-deletion-activation.v1",
        "w2_source_sha": manifest["w2_source_sha"],
        "w2_migration_head": manifest["w2_required_migration_head"],
        "w2_image_digest": image_digest,
        "command_schema_sha256": manifest["command_schema_sha256"],
        "ack_schema_sha256": manifest["ack_schema_sha256"],
        "checked_at": checked_at.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def require_w2_deletion_activation_proof(
    path: Path, *, expected_image_digest: str, now: datetime | None = None
) -> dict[str, str]:
    """Reject absent, stale, or mismatched operator proof before relay startup."""
    manifest = _w2_v2_manifest()
    if not _OCI_DIGEST.fullmatch(expected_image_digest):
        raise IntegrationPreflightError("W2 deletion image digest is invalid")
    try:
        proof = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_json_keys
        )
        if not isinstance(proof, dict) or proof.keys() != _W2_V2_PROOF_KEYS:
            raise ValueError
        checked_text = proof["checked_at"]
        if not isinstance(checked_text, str) or not checked_text.endswith("Z"):
            raise ValueError
        checked = datetime.fromisoformat(checked_text.replace("Z", "+00:00"))
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError
        age = current.astimezone(UTC) - checked.astimezone(UTC)
        if not -timedelta(minutes=1) <= age <= timedelta(minutes=10):
            raise ValueError
        expected = {
            "schema_version": "w1.w2-deletion-activation.v1",
            "w2_source_sha": manifest["w2_source_sha"],
            "w2_migration_head": manifest["w2_required_migration_head"],
            "w2_image_digest": expected_image_digest,
            "command_schema_sha256": manifest["command_schema_sha256"],
            "ack_schema_sha256": manifest["ack_schema_sha256"],
        }
        if any(proof[key] != value for key, value in expected.items()):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise IntegrationPreflightError(
            "W2 deletion activation proof is missing or invalid"
        ) from error
    return proof


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
    private_dns = host == "localhost" or "." not in host or host.endswith((".internal", ".local"))
    if not (private_ip or private_dns):
        raise IntegrationPreflightError("integration origin must be private")
