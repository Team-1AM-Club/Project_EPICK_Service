from __future__ import annotations

from pathlib import Path

COMPOSE = Path(__file__).parents[2] / "infra" / "w3-runtime.compose.yml"


def test_w3_runtime_uses_one_local_named_state_volume() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "epick-w3-core-runtime-state:" in compose
    assert "source: epick-w3-core-runtime-state" in compose
    assert "target: /state" in compose
    assert "/state/core.db" in compose
    assert "driver: local" in compose
    assert "driver_opts:" not in compose
    assert "type: bind" not in compose
    assert "nfs" not in compose.lower()


def test_w3_runtime_is_hardened_and_has_bounded_operator_services() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert 'user: "10001:10001"' in compose
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "/tmp:rw,noexec,nosuid,nodev,size=16m" in compose
    assert "w3-core-init:" in compose
    assert "w3-core-smoke:" in compose
    assert "w3-core-inspect:" in compose
    assert "w3-core-command:" in compose
