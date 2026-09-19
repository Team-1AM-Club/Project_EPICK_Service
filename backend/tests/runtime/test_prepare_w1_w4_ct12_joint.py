from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "prepare_w1_w4_ct12_joint.py"
    spec = importlib.util.spec_from_file_location("prepare_w1_w4_ct12_joint", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _environment() -> dict[str, str]:
    return {
        "W1_W4_CT12_PREPARE_ACTUAL": "YES",
        "W1_W4_CT12_SEED_DATABASE_URL": (
            "postgresql+psycopg://seed:secret@db:5432/epick_w1_w4_ct12_runtime"
        ),
        "WORKER_DATABASE_URL": (
            "postgresql+psycopg://worker:secret@db:5432/epick_w1_w4_ct12_runtime"
        ),
        "W1_W4_CT12_RUN_ID": "w1-w4-ct12-joint-01",
    }


def test_joint_configuration_accepts_only_dedicated_database(monkeypatch) -> None:
    for name, value in _environment().items():
        monkeypatch.setenv(name, value)
    script = _load_script()

    config = script._require_configuration()

    assert config.database_name == "epick_w1_w4_ct12_runtime"
    assert config.context_ttl_seconds == 3600


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("W1_W4_CT12_PREPARE_ACTUAL", "NO", "PREPARE_ACTUAL=YES"),
        (
            "WORKER_DATABASE_URL",
            "postgresql+psycopg://worker:secret@db:5432/epick_staging",
            "must target the same database",
        ),
        (
            "WORKER_DATABASE_URL",
            "postgresql+psycopg://seed:secret@db:5432/epick_w1_w4_ct12_runtime",
            "different database principals",
        ),
        ("W1_W4_CT12_RUN_ID", "joint-run-01", "must contain ct12"),
        ("W1_W4_CT12_CONTEXT_TTL_SECONDS", "299", "between 300 and 86400"),
        ("W1_W4_CT12_CONTEXT_TTL_SECONDS", "not-a-number", "must be an integer"),
    ],
)
def test_joint_configuration_fails_closed(
    monkeypatch, name: str, value: str, message: str
) -> None:
    for key, configured in _environment().items():
        monkeypatch.setenv(key, configured)
    monkeypatch.setenv(name, value)
    script = _load_script()

    with pytest.raises(script.W1W4Ct12JointPreparationError, match=message):
        script._require_configuration()


def test_joint_configuration_requires_w1_w4_ct12_database_name(monkeypatch) -> None:
    for name, value in _environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(
        "W1_W4_CT12_SEED_DATABASE_URL",
        "postgresql+psycopg://seed:secret@db:5432/epick_ct12_runtime",
    )
    monkeypatch.setenv(
        "WORKER_DATABASE_URL",
        "postgresql+psycopg://worker:secret@db:5432/epick_ct12_runtime",
    )
    script = _load_script()

    with pytest.raises(
        script.W1W4Ct12JointPreparationError, match="containing w1, w4 and ct12"
    ):
        script._require_configuration()


def test_joint_preparation_refuses_populated_database() -> None:
    script = _load_script()

    class Session:
        def scalar(self, _statement):
            return 1

    with pytest.raises(script.W1W4Ct12JointPreparationError, match="is not empty"):
        script._assert_empty_database(Session())
