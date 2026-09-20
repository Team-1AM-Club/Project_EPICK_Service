from __future__ import annotations

import json
import os
import subprocess
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    os.getenv("W3_RUNTIME_DOCKER_TEST") != "YES",
    reason="set W3_RUNTIME_DOCKER_TEST=YES with W3_RUNTIME_TEST_IMAGE to use Docker",
)
def test_w3_sqlite_state_survives_container_recreation() -> None:
    image = os.environ["W3_RUNTIME_TEST_IMAGE"]
    volume = f"epick-w3-test-{uuid4().hex}"
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
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
            image,
        ]
        initialized = subprocess.run(
            [*base, "init", "--db", "/state/core.db", "--retention-seconds", "3600"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(initialized.stdout)["status"] == "INITIALIZED_NOT_DEPLOYED"

        first = _inspect(base)
        second = _inspect(base)
        assert first == second

        repeated_init = subprocess.run(
            [*base, "init", "--db", "/state/core.db", "--retention-seconds", "3600"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert repeated_init.returncode != 0
        assert json.loads(repeated_init.stdout) == {
            "status": "FAILED",
            "code": "CORE_RUNTIME_COMMAND_FAILED",
        }
    finally:
        subprocess.run(
            ["docker", "volume", "rm", volume], check=False, capture_output=True
        )


def _inspect(base: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        [*base, "inspect", "--db", "/state/core.db", "--retention-seconds", "3600"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)
