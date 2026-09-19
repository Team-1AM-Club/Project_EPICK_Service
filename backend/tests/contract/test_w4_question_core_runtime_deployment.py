from __future__ import annotations

from pathlib import Path

COMPOSE = Path(__file__).parents[2] / "infra" / "w1-runtime.compose.yml"


def test_w4_question_core_profile_requires_a_narrow_env_file_and_ca_mount() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "w4-question-core-decision-worker:" in compose
    assert "W1_W4_QUESTION_CORE_ENV_FILE:?set W1_W4_QUESTION_CORE_ENV_FILE" in compose
    assert 'command: ["python", "scripts/run_w4_question_core_decision_worker.py"]' in compose
    assert 'profiles: ["w4-question-core"]' in compose
    assert "RDS_CA_BUNDLE_PATH:?set RDS_CA_BUNDLE_PATH" in compose
    assert "target: /run/epick/rds-ca.pem" in compose
    assert "create_host_path: false" in compose
    assert "restart: unless-stopped" in compose
    assert "read_only: true" in compose
