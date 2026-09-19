from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _load_runner():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_w1_w4_ct12_synthetic.py"
    spec = importlib.util.spec_from_file_location("run_w1_w4_ct12_synthetic", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _environment() -> dict[str, str]:
    return {
        "W1_W4_CT12_EXECUTE_SYNTHETIC": "YES",
        "W1_W4_CT12_SEED_DATABASE_URL": (
            "postgresql+psycopg://seed:secret@127.0.0.1:5543/epick_w1_w4_ct12_runtime"
        ),
        "WORKER_DATABASE_URL": (
            "postgresql+psycopg://worker:secret@127.0.0.1:5543/epick_w1_w4_ct12_runtime"
        ),
        "AWS_DEFAULT_REGION": "ap-northeast-2",
        "W4_QUESTION_CORE_DECISION_QUEUE_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-ct12-w4-question-core"
        ),
        "W4_QUESTION_CORE_DECISION_DLQ_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-ct12-w4-question-core-dlq"
        ),
        "W4_QUESTION_CORE_DECISION_EXPECTED_PRODUCER": "w4",
        "W4_QUESTION_CORE_DECISION_EXPECTED_SENDER_ID": "AROAW4CT12SENDER",
        "W1_W4_CT12_SENDER_ROLE_ARN": (
            "arn:aws:iam::123456789012:role/epick-isolated-w4-ct12-sender"
        ),
        "W1_W4_CT12_RUN_ID": "w1-w4-ct12-runtime-01",
    }


def test_ct12_configuration_requires_isolated_database_and_queues(monkeypatch) -> None:
    for name, value in _environment().items():
        monkeypatch.setenv(name, value)
    runner = _load_runner()

    config = runner._require_configuration()

    assert config.run_id == "w1-w4-ct12-runtime-01"
    assert config.database_name == "epick_w1_w4_ct12_runtime"
    assert config.queue_url.endswith("epick-ct12-w4-question-core")


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("W1_W4_CT12_EXECUTE_SYNTHETIC", "NO", "EXECUTE_SYNTHETIC=YES"),
        (
            "WORKER_DATABASE_URL",
            "postgresql+psycopg://worker:secret@db/epick_staging",
            "must target the same database",
        ),
        (
            "WORKER_DATABASE_URL",
            "postgresql+psycopg://seed:secret@127.0.0.1:5543/epick_w1_w4_ct12_runtime",
            "different database principals",
        ),
        (
            "W4_QUESTION_CORE_DECISION_QUEUE_URL",
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/shared-w4-queue",
            "dedicated queue containing w4 and ct12",
        ),
        (
            "W4_QUESTION_CORE_DECISION_EXPECTED_SENDER_ID",
            "AROAW4CT12SENDER:session",
            "without session",
        ),
    ],
)
def test_ct12_configuration_fails_closed(monkeypatch, name: str, value: str, error: str) -> None:
    for key, configured in _environment().items():
        monkeypatch.setenv(key, configured)
    monkeypatch.setenv(name, value)
    runner = _load_runner()

    with pytest.raises(runner.W1W4Ct12ScenarioError, match=error):
        runner._require_configuration()


def test_synthetic_w4_event_is_strict_contract() -> None:
    runner = _load_runner()
    seed = SimpleNamespace(
        job_id=uuid4(),
        source_id=uuid4(),
        question_version_id=uuid4(),
        analysis_input_version="ct12-apply-v1",
    )

    event = runner._event(seed=seed, run_id="w1-w4-ct12-runtime-01", scenario="apply")
    parsed = runner.parse_w4_question_core_event(event)

    assert parsed.producer == "w4"
    assert parsed.company_id is None
    assert parsed.question_version_id == seed.question_version_id
