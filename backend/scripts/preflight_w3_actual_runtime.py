"""Run M1-only Docker checks without AWS or private runtime configuration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.runtime.w3_runtime_preflight import (  # noqa: E402
    W3RuntimeComposeInspection,
    W3RuntimeImageInspection,
    W3RuntimePersistenceInspection,
    W3RuntimePreflightConfig,
    W3RuntimePreflightError,
    verify_w3_actual_runtime,
)

RETENTION_SECONDS = "1209600"
POLICY_REVISION = "w3.retention/1.1"


def main() -> None:
    image_ref = _required_env("W3_IMAGE")
    compose_path = Path(_required_env("W3_RUNTIME_COMPOSE_FILE")).resolve()
    config = W3RuntimePreflightConfig(
        expected_implementation_sha=_required_env("W3_EXPECTED_IMPLEMENTATION_SHA"),
        expected_receipt_head_sha=_required_env("W3_EXPECTED_RECEIPT_HEAD_SHA"),
        expected_policy_revision=POLICY_REVISION,
        expected_image_digest=_required_env("W3_EXPECTED_IMAGE_DIGEST"),
        expected_runtime_uid=int(os.getenv("W3_RUNTIME_UID", "10001")),
        expected_state_volume=os.getenv(
            "W3_RUNTIME_STATE_VOLUME", "epick-w3-core-runtime-state"
        ),
        database_path="/state/core.db",
    )
    image = _inspect_image(image_ref, config.expected_image_digest)
    compose = _inspect_compose(compose_path, image_ref)
    persistence = _inspect_ephemeral_persistence(image_ref)
    result = verify_w3_actual_runtime(
        config=config,
        image=image,
        compose=compose,
        persistence=persistence,
    )
    print(json.dumps(result.as_safe_dict(), separators=(",", ":")))


def _inspect_image(image_ref: str, expected_digest: str) -> W3RuntimeImageInspection:
    raw = _run_json(["docker", "image", "inspect", image_ref])
    if not isinstance(raw, list) or len(raw) != 1:
        raise W3RuntimePreflightError("Docker returned an invalid image inspection")
    item = raw[0]
    labels = item.get("Config", {}).get("Labels") or {}
    candidates = {item.get("Id", "")}
    candidates.update(value.rsplit("@", 1)[-1] for value in item.get("RepoDigests") or [])
    if expected_digest not in candidates:
        raise W3RuntimePreflightError("W3 image digest does not match the pinned image")
    return W3RuntimeImageInspection(
        implementation_sha=labels.get("org.opencontainers.image.revision", ""),
        receipt_head_sha=labels.get("io.epick.w3.receipt-head", ""),
        policy_revision=labels.get("io.epick.w3.policy-revision", ""),
        image_digest=expected_digest,
        runtime_user=item.get("Config", {}).get("User", ""),
    )


def _inspect_compose(compose_path: Path, image_ref: str) -> W3RuntimeComposeInspection:
    env = os.environ.copy()
    env["W3_IMAGE"] = image_ref
    raw = _run_json(
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "--profile",
            "*",
            "config",
            "--format",
            "json",
        ],
        env=env,
    )
    try:
        service = raw["services"]["w3-core-command"]
        volumes = service["volumes"]
        state_mount = next(row for row in volumes if row.get("target") == "/state")
    except (KeyError, TypeError, StopIteration) as error:
        raise W3RuntimePreflightError("W3 Compose inspection is incomplete") from error
    return W3RuntimeComposeInspection(
        runtime_user=str(service.get("user", "")),
        read_only=service.get("read_only") is True,
        state_volume=str(state_mount.get("source", "")),
        state_target=str(state_mount.get("target", "")),
        cap_drop=tuple(service.get("cap_drop") or ()),
        tmpfs=tuple(service.get("tmpfs") or ()),
    )


def _inspect_ephemeral_persistence(image_ref: str) -> W3RuntimePersistenceInspection:
    volume = f"epick-w3-m1-preflight-{uuid4().hex}"
    _run(["docker", "volume", "create", volume])
    try:
        base = [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount",
            f"type=volume,source={volume},target=/state",
            image_ref,
        ]
        smoke = _run_json(
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m",
                image_ref,
                "smoke",
                "--directory",
                "/tmp/smoke",
            ]
        )
        initialized = _run_json(
            [
                *base,
                "init",
                "--db",
                "/state/core.db",
                "--retention-seconds",
                RETENTION_SECONDS,
            ]
        )
        before_report = _run_json(_inspect_command(base))
        after_report = _run_json(_inspect_command(base))
        before = _count_only(before_report)
        after = _count_only(after_report)
        policy_revision = _policy_revision(before_report, after_report)
        migration_blockers = _migration_blockers(before_report, after_report)
        return W3RuntimePersistenceInspection(
            database_path="/state/core.db",
            initialized=initialized.get("status") == "INITIALIZED_NOT_DEPLOYED",
            smoke_status=str(smoke.get("status", "")),
            smoke_aws_calls=int(smoke.get("aws_calls", -1)),
            policy_revision=policy_revision,
            migration_blockers=migration_blockers,
            before_counts=before,
            after_counts=after,
        )
    finally:
        subprocess.run(
            ["docker", "volume", "rm", volume],
            check=False,
            capture_output=True,
            text=True,
        )


def _inspect_command(base: list[str]) -> list[str]:
    return [
        *base,
        "inspect",
        "--db",
        "/state/core.db",
        "--retention-seconds",
        RETENTION_SECONDS,
    ]


def _count_only(value: dict[str, Any]) -> dict[str, int]:
    deliveries = value.get("deliveries")
    if not isinstance(deliveries, list):
        raise W3RuntimePreflightError("W3 inspect response is invalid")
    counts: dict[str, int] = {"deliveries": len(deliveries)}
    for delivery in deliveries:
        state = delivery.get("state") if isinstance(delivery, dict) else None
        if isinstance(state, str):
            key = f"state_{state.lower()}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def _policy_revision(before: dict[str, Any], after: dict[str, Any]) -> str:
    before_revision = before.get("policy_revision")
    after_revision = after.get("policy_revision")
    if before_revision != after_revision or not isinstance(before_revision, str):
        raise W3RuntimePreflightError("W3 inspect policy revision is invalid")
    return before_revision


def _migration_blockers(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, int]:
    before_blockers = before.get("migration_blockers")
    after_blockers = after.get("migration_blockers")
    if before_blockers != after_blockers or not isinstance(before_blockers, dict):
        raise W3RuntimePreflightError("W3 inspect migration blockers are invalid")
    if any(
        not isinstance(name, str) or type(value) is not int
        for name, value in before_blockers.items()
    ):
        raise W3RuntimePreflightError("W3 inspect migration blockers are invalid")
    return dict(before_blockers)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise W3RuntimePreflightError(f"missing required setting: {name}")
    return value


def _run_json(command: list[str], *, env: dict[str, str] | None = None) -> Any:
    completed = _run(command, env=env)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise W3RuntimePreflightError("runtime command returned invalid JSON") from error


def _run(
    command: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise W3RuntimePreflightError("runtime command failed") from error


if __name__ == "__main__":
    try:
        main()
    except (ValueError, W3RuntimePreflightError) as error:
        raise SystemExit(str(error)) from error
