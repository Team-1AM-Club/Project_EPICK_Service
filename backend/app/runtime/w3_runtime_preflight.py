"""Secret-safe M1 checks for the immutable W3 local-state runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_POLICY_REVISION = "w3.retention/1.1"


class W3RuntimePreflightError(RuntimeError):
    """Safe operator-facing failure that never contains runtime values."""


@dataclass(frozen=True, slots=True)
class W3RuntimePreflightConfig:
    expected_implementation_sha: str
    expected_receipt_head_sha: str
    expected_policy_revision: str
    expected_image_digest: str
    expected_runtime_uid: int
    expected_state_volume: str
    database_path: str


@dataclass(frozen=True, slots=True)
class W3RuntimeImageInspection:
    implementation_sha: str
    receipt_head_sha: str
    policy_revision: str
    image_digest: str
    runtime_user: str


@dataclass(frozen=True, slots=True)
class W3RuntimeComposeInspection:
    runtime_user: str
    read_only: bool
    state_volume: str
    state_target: str
    cap_drop: tuple[str, ...]
    tmpfs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class W3RuntimePersistenceInspection:
    database_path: str
    initialized: bool
    smoke_status: str
    smoke_aws_calls: int
    policy_revision: str
    migration_blockers: Mapping[str, int]
    before_counts: Mapping[str, int]
    after_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class W3RuntimePreflightResult:
    def as_safe_dict(self) -> dict[str, str]:
        return {
            "status": "ok",
            "image": "implementation_receipt_and_digest_verified",
            "runtime": "non_root_read_only",
            "state": "single_local_volume_persisted",
            "smoke": "local_no_network_verified",
            "policy": "w3_retention_1_1_verified",
        }


def verify_w3_actual_runtime(
    *,
    config: W3RuntimePreflightConfig,
    image: W3RuntimeImageInspection,
    compose: W3RuntimeComposeInspection,
    persistence: W3RuntimePersistenceInspection,
) -> W3RuntimePreflightResult:
    """Validate collected Docker evidence without making any network request."""

    _validate_config(config)
    if image.implementation_sha != config.expected_implementation_sha:
        raise W3RuntimePreflightError(
            "W3 implementation revision does not match the reviewed input"
        )
    if image.receipt_head_sha != config.expected_receipt_head_sha:
        raise W3RuntimePreflightError("W3 receipt HEAD does not match the reviewed input")
    if image.policy_revision != config.expected_policy_revision:
        raise W3RuntimePreflightError("W3 image policy revision does not match")
    if image.image_digest != config.expected_image_digest:
        raise W3RuntimePreflightError("W3 image digest does not match the pinned image")

    expected_user = f"{config.expected_runtime_uid}:{config.expected_runtime_uid}"
    if image.runtime_user != expected_user or compose.runtime_user != expected_user:
        raise W3RuntimePreflightError("W3 runtime user is not the expected non-root UID/GID")
    if not compose.read_only:
        raise W3RuntimePreflightError("W3 runtime root filesystem must be read-only")
    if compose.state_volume != config.expected_state_volume or compose.state_target != "/state":
        raise W3RuntimePreflightError("W3 runtime state mount is not the approved local volume")
    if set(compose.cap_drop) != {"ALL"}:
        raise W3RuntimePreflightError("W3 runtime must drop all Linux capabilities")
    if "/tmp:rw,noexec,nosuid,nodev,size=16m" not in compose.tmpfs:
        raise W3RuntimePreflightError("W3 runtime tmpfs is not bounded and hardened")

    if not persistence.initialized or persistence.database_path != config.database_path:
        raise W3RuntimePreflightError("W3 SQLite database was not initialized at /state/core.db")
    if persistence.smoke_status != "LOCAL_VERIFIED_NOT_DEPLOYED":
        raise W3RuntimePreflightError("W3 local smoke did not reach its verified state")
    if persistence.smoke_aws_calls != 0:
        raise W3RuntimePreflightError("W3 local smoke attempted a network call")
    if persistence.policy_revision != config.expected_policy_revision:
        raise W3RuntimePreflightError("W3 persisted policy revision does not match")
    blockers = dict(persistence.migration_blockers)
    if set(blockers) != {"owner_tombstone_without_deleted_at"} or any(
        type(value) is not int or value != 0 for value in blockers.values()
    ):
        raise W3RuntimePreflightError("W3 runtime has an unresolved migration blocker")
    before = _validated_counts(persistence.before_counts)
    after = _validated_counts(persistence.after_counts)
    if before != after:
        raise W3RuntimePreflightError("W3 restart persistence counts do not match")
    return W3RuntimePreflightResult()


def _validate_config(config: W3RuntimePreflightConfig) -> None:
    if not _SHA_PATTERN.fullmatch(config.expected_implementation_sha):
        raise W3RuntimePreflightError("expected W3 implementation revision is invalid")
    if not _SHA_PATTERN.fullmatch(config.expected_receipt_head_sha):
        raise W3RuntimePreflightError("expected W3 receipt HEAD is invalid")
    if config.expected_policy_revision != _POLICY_REVISION:
        raise W3RuntimePreflightError("expected W3 policy revision is invalid")
    if not _DIGEST_PATTERN.fullmatch(config.expected_image_digest):
        raise W3RuntimePreflightError("expected W3 image digest is invalid")
    if config.expected_runtime_uid <= 0:
        raise W3RuntimePreflightError("expected W3 runtime UID must be non-root")
    if not config.expected_state_volume:
        raise W3RuntimePreflightError("expected W3 state volume is missing")
    if config.database_path != "/state/core.db":
        raise W3RuntimePreflightError("expected W3 database path must be /state/core.db")


def _validated_counts(value: Mapping[str, int]) -> dict[str, int]:
    counts = dict(value)
    if not counts or any(
        not isinstance(name, str)
        or not name
        or type(count) is not int
        or count < 0
        for name, count in counts.items()
    ):
        raise W3RuntimePreflightError("W3 persistence counts are invalid")
    return counts
