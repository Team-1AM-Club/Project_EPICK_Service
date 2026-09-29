"""Static source-runtime image and Compose contract; never invoke Docker."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _artifact(name: str) -> str:
    path = PROJECT_ROOT / name
    assert path.is_file(), f"required source-runtime artifact is missing: {name}"
    return path.read_text(encoding="utf-8")


def test_source_runtime_dockerfile_uses_only_immutable_runtime_inputs() -> None:
    dockerfile = _artifact("Dockerfile.source-runtime")

    assert "ARG PYTHON_IMAGE" in dockerfile
    assert "FROM ${PYTHON_IMAGE}" in dockerfile
    assert re.search(r"@sha256:\[a-f0-9\]\{64\}", dockerfile)
    assert re.search(r"\[a-f0-9\]\{40\}", dockerfile)
    assert "requirements.ct15.lock" in dockerfile
    assert "--require-hashes --no-deps -r requirements.ct15.lock" in dockerfile
    assert "PYTHONPATH=/app/src" in dockerfile
    assert (
        'ENTRYPOINT ["python", "-m", "epick_engine.source_collection.source_runtime_operator"]'
        in dockerfile
    )
    assert 'CMD ["preflight"]' in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "pip install ." not in dockerfile
    assert "pyproject.toml" not in dockerfile
    assert "contracts/" not in dockerfile


def test_source_runtime_compose_uses_hardened_windows_safe_read_only_mounts() -> None:
    compose = _artifact("compose.source-runtime.yaml")

    assert "profiles: [source-runtime]" in compose
    assert "read_only: true" in compose
    assert "cap_drop: [ALL]" in compose
    assert "no-new-privileges:true" in compose
    assert "/tmp:rw,noexec,nosuid,size=16m" in compose
    assert "type: bind" in compose
    assert "source: ${W2_SOURCE_RUNTIME_CONFIG_HOST_FILE:?" in compose
    assert "target: /run/epick/source-runtime/config.json" in compose
    assert "source: ${W1_LOOKUP_CA_HOST_FILE:?" in compose
    assert "target: /run/epick/source-runtime/w1-ca.pem" in compose
    assert compose.count("read_only: true") >= 3
    assert "W2_SOURCE_RUNTIME_CONFIG_FILE: /run/epick/source-runtime/config.json" in compose
    assert "W1_LOOKUP_CA_FILE: /run/epick/source-runtime/w1-ca.pem" in compose
    assert "C:\\" not in compose


def test_source_runtime_compose_requires_every_runtime_setting_without_literals() -> None:
    compose = _artifact("compose.source-runtime.yaml")
    required = {
        "EPICK_DATABASE_URL",
        "W2_SOURCE_RUNTIME_CONFIG_FILE",
        "W1_LOOKUP_ENDPOINT",
        "W1_LOOKUP_BEARER",
        "W1_LOOKUP_CA_FILE",
        "W1_COLLECTION_COMMAND_QUEUE_URL",
        "W1_COMMIT_GATE_COMMAND_QUEUE_URL",
        "W1_PRIVATE_INBOUND_QUEUE_URL",
        "W1_EXPECTED_SYSTEM_SENDER_ID",
    }

    for name in required - {"W2_SOURCE_RUNTIME_CONFIG_FILE", "W1_LOOKUP_CA_FILE"}:
        assert f"{name}: ${{{name}:?" in compose

    lowered = compose.lower()
    assert "postgresql://" not in lowered
    assert "postgresql+" not in lowered
    assert "akia" not in lowered
    assert not re.search(r"\b\d{12}\b", compose)
    assert "https://sqs." not in lowered
    for forbidden in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
    ):
        assert forbidden not in compose


def test_source_runtime_compose_runs_a_daemon_with_health_restart_and_shutdown() -> None:
    compose = _artifact("compose.source-runtime.yaml")

    assert "command: [run]" in compose
    assert "healthcheck:" in compose
    assert re.search(r"(?m)^\s*restart:\s*(?:unless-stopped|on-failure)", compose)
    assert re.search(r"(?m)^\s*stop_signal:\s*SIGTERM", compose)
    assert "stop_grace_period: 12h5m" in compose
