from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import configure_mappers

from app.models.deletion import DeletionRequest  # noqa: F401
from app.models.identity import User  # noqa: F401
from app.models.jobs import JobCommand, OutboxMessage  # noqa: F401
from app.runtime.workers import _validate


def _load_runner():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_w1_t060_synthetic.py"
    spec = importlib.util.spec_from_file_location("run_w1_t060_synthetic", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_t060_synthetic_runner_registers_outbox_foreign_key_models() -> None:
    runner_path = Path(__file__).resolve().parents[2] / "scripts" / "run_w1_t060_synthetic.py"
    assert "from app.models.deletion import DeletionRequest" in runner_path.read_text(
        encoding="utf-8"
    )
    configure_mappers()


def test_t060_result_fixture_keeps_command_binding_and_checkpoint_optional() -> None:
    runner = _load_runner()
    command = JobCommand(
        id=uuid4(),
        job_id=uuid4(),
        owner_user_id=uuid4(),
        command_type="W2_SOURCE_COLLECTION",
        command_schema_version="1.0",
        command_sequence=2,
        execution_fence=1,
        owner_deletion_epoch=0,
        analysis_input_version="t060-input:v1",
        payload={
            "w2_command": {
                "input_version": 1,
                "source_id": str(uuid4()),
            }
        },
    )
    message_id = uuid4()

    complete = runner._result_envelope(command=command, message_id=message_id)
    partial = runner._result_envelope(command=command, message_id=uuid4(), partial=True)

    assert complete["message_id"] == str(message_id)
    assert complete["payload"]["command_id"] == str(command.id)
    assert complete["payload"]["checkpoint_ref"] is None
    assert partial["payload"]["completion_kind"] == "partial"
    assert partial["payload"]["resume_stage"] == "fetch"
    assert partial["payload"]["checkpoint_ref"].endswith("checkpoint-1")
    _validate(
        complete,
        "w1/v1/private-message-envelope.schema.json",
        "RESULT_ENVELOPE_INVALID",
    )
    _validate(
        complete["payload"],
        "w2/v1/source-collection.result.schema.json",
        "RESULT_SCHEMA_INVALID",
    )
    _validate(
        partial["payload"],
        "w2/v1/source-collection.result.schema.json",
        "RESULT_SCHEMA_INVALID",
    )


def test_dispatch_publishes_w2_command_before_a_synthetic_result_can_run(monkeypatch) -> None:
    runner = _load_runner()
    calls: list[str] = []
    relay_status = {
        "status": "ok",
        "claimed": 1,
        "published": 1,
        "retry_scheduled": 0,
        "failed_final": 0,
        "stale_completion": 0,
    }

    def fake_child(phase: str, **_kwargs):
        calls.append(phase)
        if phase == "relay-drain":
            return dict(relay_status)
        return {"status": "ok", "received": 1, "acknowledged": 1}

    running_job = SimpleNamespace(status="RUNNING", active_lease_id=uuid4())

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _model, _identity):
            return running_job

    command = object()
    monkeypatch.setattr(runner, "_run_child", fake_child)
    monkeypatch.setattr(runner, "_get_w2_command", lambda **_kwargs: command)
    seeded = runner.SeededJob(
        owner_id=uuid4(),
        job_id=uuid4(),
        execution_command_id=uuid4(),
        execution_outbox_id=uuid4(),
    )

    returned = runner._dispatch_job(
        config=object(),
        seed_factory=FakeSession,
        seeded=seeded,
    )

    assert returned is command
    assert calls == ["relay-drain", "job-once", "relay-drain"]
