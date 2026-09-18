from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _load_runner():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_w1_t043_synthetic.py"
    spec = importlib.util.spec_from_file_location("run_w1_t043_synthetic", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _environment() -> dict[str, str]:
    return {
        "T043_EXECUTE_SYNTHETIC": "YES",
        "T043_SEED_DATABASE_URL": (
            "postgresql+psycopg://seed:secret@127.0.0.1:5543/epick_t043_runtime"
        ),
        "WORKER_DATABASE_URL": (
            "postgresql+psycopg://worker:secret@127.0.0.1:5543/epick_t043_runtime"
        ),
        "AWS_DEFAULT_REGION": "ap-northeast-2",
        "W3_CORE_DECISION_QUEUE_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t043-core-decision"
        ),
        "W3_CORE_DECISION_DLQ_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t043-core-decision-dlq"
        ),
        "W3_CORE_DECISION_EXPECTED_PRODUCER": "w3",
        "W3_CORE_DECISION_EXPECTED_SENDER_ID": "AROATESTROLEID1234567",
        "T043_SENDER_ROLE_ARN": (
            "arn:aws:iam::123456789012:role/epick-staging-t043-w3-sender-role"
        ),
        "T043_RUN_ID": "t043-runtime-01",
    }


def test_t043_configuration_requires_disposable_database_and_queues(monkeypatch) -> None:
    runner = _load_runner()
    for name, value in _environment().items():
        monkeypatch.setenv(name, value)

    config = runner._require_configuration()

    assert config.run_id == "t043-runtime-01"
    assert config.database_name == "epick_t043_runtime"
    assert config.queue_url.endswith("epick-t043-core-decision")
    assert config.dlq_url.endswith("epick-t043-core-decision-dlq")


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("T043_EXECUTE_SYNTHETIC", "NO", "T043_EXECUTE_SYNTHETIC=YES"),
        (
            "WORKER_DATABASE_URL",
            "postgresql+psycopg://worker:secret@db/epick_staging",
            "must target the same database",
        ),
        (
            "W3_CORE_DECISION_QUEUE_URL",
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/shared-core-decision",
            "dedicated queue containing t043",
        ),
        (
            "W3_CORE_DECISION_EXPECTED_SENDER_ID",
            "AROATESTROLEID1234567:session",
            "stable role id without session",
        ),
    ],
)
def test_t043_configuration_fails_closed(monkeypatch, name: str, value: str, error: str) -> None:
    runner = _load_runner()
    for key, configured in _environment().items():
        monkeypatch.setenv(key, configured)
    monkeypatch.setenv(name, value)

    with pytest.raises(runner.T043ScenarioError, match=error):
        runner._require_configuration()


def test_t043_configuration_rejects_shared_database_even_for_two_principals(monkeypatch) -> None:
    runner = _load_runner()
    for key, configured in _environment().items():
        monkeypatch.setenv(key, configured)
    monkeypatch.setenv(
        "T043_SEED_DATABASE_URL",
        "postgresql+psycopg://seed:secret@db/epick_staging",
    )
    monkeypatch.setenv(
        "WORKER_DATABASE_URL",
        "postgresql+psycopg://worker:secret@db/epick_staging",
    )

    with pytest.raises(runner.T043ScenarioError, match="dedicated database containing t043"):
        runner._require_configuration()


def test_recording_sqs_port_retains_last_delivery_for_visibility_release() -> None:
    runner = _load_runner()
    delivery = SimpleNamespace(receipt_handle="receipt-1")

    class Delegate:
        def receive_messages(self, **_kwargs):
            return [delivery]

        def delete_message(self, **_kwargs):
            return None

        def change_message_visibility(self, **_kwargs):
            return None

        def get_queue_attributes(self, **_kwargs):
            return {"QueueArn": "arn:test"}

    port = runner.RecordingSqsPort(Delegate())

    assert port.receive_messages(
        queue_url="https://queue.example/t043",
        max_messages=1,
        visibility_timeout_seconds=120,
        wait_time_seconds=1,
    ) == [delivery]
    assert port.last_deliveries == [delivery]


def test_synthetic_event_is_strict_w3_contract() -> None:
    runner = _load_runner()
    seed = SimpleNamespace(
        job_id=uuid4(),
        company_id=uuid4(),
        source_id=uuid4(),
        analysis_input_version="t043-apply-v1",
    )

    event = runner._core_event(seed=seed, run_id="t043-runtime-01", scenario="apply")
    parsed = runner.parse_w3_core_decision_event(event)

    assert parsed.producer == "w3"
    assert parsed.job_id == seed.job_id
    assert parsed.decision_code == "CORE_REQUIRED"
