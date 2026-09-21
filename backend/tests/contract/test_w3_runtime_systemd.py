from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[3]
SYSTEMD = ROOT / "backend" / "infra" / "systemd"


def test_w3_runtime_service_runs_only_bounded_operations_and_inspection() -> None:
    service = (SYSTEMD / "epick-w3-core-runtime.service").read_text(encoding="utf-8")

    assert "Type=oneshot" in service
    assert "w3-core-relay" in service
    assert "w3-core-lifecycle" in service
    assert service.index("w3-core-lifecycle") < service.index("w3-core-relay")
    assert "w3-core-expire" in service
    assert "inspect_w3_core_runtime.py" in service
    assert "TimeoutStartSec=240" in service
    assert "restart=" not in service.lower()
    assert "W3_IMAGE=" not in service
    assert "QUEUE_URL=" not in service
    assert "ROLE_ID=" not in service


def test_w3_runtime_timer_has_five_minute_bounded_cadence() -> None:
    timer = (SYSTEMD / "epick-w3-core-runtime.timer").read_text(encoding="utf-8")

    assert "OnUnitInactiveSec=5min" in timer
    assert "Persistent=true" in timer
    assert "Unit=epick-w3-core-runtime.service" in timer
