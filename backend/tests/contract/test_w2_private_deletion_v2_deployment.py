"""W1's v2 deletion processes must not inherit the general worker boundary."""

from __future__ import annotations

import re
from pathlib import Path

COMPOSE_PATH = Path(__file__).parents[2] / "infra" / "w1-runtime.compose.yml"


def _service(compose: str, name: str) -> str:
    return re.split(r"\n  [a-z][a-z0-9-]*:\n", compose.split(f"  {name}:\n", 1)[1], maxsplit=1)[0]


def test_v2_relay_has_isolated_profile_and_activation_proof_mount() -> None:
    section = _service(COMPOSE_PATH.read_text(encoding="utf-8"), "w1-w2-deletion-command-relay")

    assert "W1_W2_DELETION_RELAY_ENV_FILE" in section
    assert '["python", "scripts/run_outbox_relay.py", "--scope", "w2-deletion"]' in section
    assert "W2_DELETION_ACTIVATION_PROOF_PATH" in section
    assert "target: /run/epick/w2-deletion-activation.json" in section
    assert 'profiles: ["w2-deletion"]' in section
    assert "W1_RUNTIME_ENV_FILE" not in section
    assert "ports:" not in section
    assert "read_only: true" in section
    assert "- ALL" in section


def test_v2_callback_is_private_and_uses_separate_env() -> None:
    section = _service(COMPOSE_PATH.read_text(encoding="utf-8"), "w1-w2-deletion-callback")

    assert "W1_W2_DELETION_CALLBACK_ENV_FILE" in section
    assert '["python", "scripts/run_w2_deletion_callback.py"]' in section
    assert 'expose:\n      - "8081"' in section
    assert 'profiles: ["w2-deletion"]' in section
    assert "W1_RUNTIME_ENV_FILE" not in section
    assert "ports:" not in section
    assert "read_only: true" in section
    assert "- ALL" in section
