import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from pydantic import ValidationError

from w3_knowledge.core_decision import (
    CoreDecisionEvent,
    CoreDecisionProducer,
    DecisionContext,
    validate_current_binding,
)


def context(**changes):
    return DecisionContext.model_validate(
        {
            "job_id": "10000000-0000-4000-8000-000000000001",
            "company_id": "20000000-0000-4000-8000-000000000001",
            "source_id": "30000000-0000-4000-8000-000000000001",
            "analysis_input_version": "knowledge-input:alpha",
            **changes,
        }
    )


def publish(producer, key="request-1", requested=None, current=None, **changes):
    return producer.publish(
        requested=requested or context(),
        current=current or context(),
        authenticated_principal="w3",
        idempotency_key=key,
        decision_code="CORE_REQUIRED",
        reason_code="REQUIRED_COMPANY_EVIDENCE",
        **changes,
    )


def test_restart_retry_preserves_event_and_revision(tmp_path):
    path = tmp_path / "producer.db"
    event = publish(CoreDecisionProducer(path))
    retried = publish(CoreDecisionProducer(path))
    assert retried == event
    assert event.decision_version == 1
    assert event.analysis_input_version == "knowledge-input:alpha"
    assert event.decision_owner == "W3" and event.producer == "w3"
    assert event.question_version_id is None
    assert CoreDecisionProducer(path).pending(context()) == [event]


def test_idempotency_conflict_has_no_extra_event(tmp_path):
    producer = CoreDecisionProducer(tmp_path / "p.db")
    event = publish(producer)
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        producer.publish(
            requested=context(),
            current=context(),
            authenticated_principal="w3",
            idempotency_key="request-1",
            decision_code="NON_CORE_OPTIONAL",
            reason_code="OPTIONAL_CONTEXT",
        )
    assert producer.pending(context()) == [event]


@pytest.mark.parametrize(
    "field,value",
    [
        ("analysis_input_version", "new-input"),
        ("source_id", "30000000-0000-4000-8000-000000000002"),
        ("company_id", "20000000-0000-4000-8000-000000000002"),
        ("job_id", "10000000-0000-4000-8000-000000000002"),
    ],
)
def test_wrong_current_binding_does_not_persist(tmp_path, field, value):
    producer = CoreDecisionProducer(tmp_path / "p.db")
    with pytest.raises(ValueError, match="CURRENT_BINDING_MISMATCH"):
        publish(producer, current=context(**{field: value}))
    assert producer.pending(context()) == []
    assert publish(producer).decision_version == 1


def test_old_outbox_input_is_not_returned_for_new_input(tmp_path):
    producer = CoreDecisionProducer(tmp_path / "p.db")
    old = publish(producer)
    new_context = context(analysis_input_version="beta")
    assert producer.pending(new_context) == []
    new = publish(producer, key="new", requested=new_context, current=new_context)
    assert new.decision_version == 2
    assert producer.pending(new_context) == [new]
    with pytest.raises(ValueError, match="CURRENT_BINDING_MISMATCH"):
        validate_current_binding(old, current=new_context, authenticated_principal="w3")


def test_non_core_pair_and_forged_principal(tmp_path):
    producer = CoreDecisionProducer(tmp_path / "p.db")
    kwargs = dict(
        requested=context(),
        current=context(),
        idempotency_key="optional",
        decision_code="NON_CORE_OPTIONAL",
        reason_code="OPTIONAL_CONTEXT",
    )
    with pytest.raises(ValueError, match="UNAUTHENTICATED_PRODUCER"):
        producer.publish(**kwargs, authenticated_principal="w4")
    event = producer.publish(**kwargs, authenticated_principal="w3")
    assert event.is_core is False and event.decision_version == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"decision_owner": "W4"},
        {"producer": "w4"},
        {"decision_scope": "QUESTION_MATCHING"},
        {"is_core": False},
        {"decision_version": True},
        {"decision_version": 0},
        {"question_version_id": "40000000-0000-4000-8000-000000000001"},
        {"reason_code": "free text"},
        {"company_id": None},
        {"unexpected": "value"},
    ],
)
def test_rejects_forged_wire_fields(tmp_path, changes):
    event = publish(CoreDecisionProducer(tmp_path / "p.db"))
    with pytest.raises(ValidationError):
        CoreDecisionEvent.model_validate({**event.model_dump(mode="json"), **changes})


def test_concurrent_connections_allocate_unique_revisions(tmp_path):
    path = tmp_path / "p.db"
    CoreDecisionProducer(path)

    def run(n):
        return publish(CoreDecisionProducer(path), key=f"request-{n}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        events = list(pool.map(run, range(8)))
    assert sorted(e.decision_version for e in events) == list(range(1, 9))
    assert len({e.message_id for e in events}) == 8


def test_event_schema_roundtrip(tmp_path):
    from jsonschema import Draft202012Validator, FormatChecker

    event = publish(CoreDecisionProducer(tmp_path / "p.db"))
    wire = json.loads(event.model_dump_json())
    Draft202012Validator(
        CoreDecisionEvent.model_json_schema(), format_checker=FormatChecker()
    ).validate(wire)
    assert UUID(wire["message_id"])


def test_actual_pinned_w1_core_validator_accepts_numeric_projection(tmp_path):
    import hashlib
    import importlib.util
    from pathlib import Path

    fixture = Path(__file__).parents[1] / "fixtures/w1_core_decision_pin"
    manifest = json.loads((fixture / "manifest.json").read_text())
    for name, info in manifest["files"].items():
        assert hashlib.sha256((fixture / name).read_bytes()).hexdigest() == info["sha256"]
    spec = importlib.util.spec_from_file_location(
        "pinned_w1_binding", fixture / "core_decision_binding.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    event = publish(CoreDecisionProducer(tmp_path / "p.db"))
    pin = {
        field: event.model_dump(mode="json")[field]
        for field in (
            "decision_scope",
            "company_id",
            "question_version_id",
            "source_id",
            "analysis_input_version",
            "decision_version",
            "is_core",
            "decision_code",
            "reason_code",
        )
    }
    command = {
        "source_id": str(event.source_id),
        "company_id": str(event.company_id),
        "input_version": 1,
        "core_source_decision": {
            "is_core": True,
            "decided_by": "W3",
            "rationale": "REQUIRED_COMPANY_EVIDENCE",
            "decision_revision": 1,
            "analysis_input_version": 1,
        },
    }
    module.validate_core_pin_payload_binding(pin=pin, w2_command=command)
    # Using the opaque input string in the numeric W2 field must fail.
    command["core_source_decision"]["analysis_input_version"] = event.analysis_input_version
    with pytest.raises(module.CoreDecisionBindingError):
        module.validate_core_pin_payload_binding(pin=pin, w2_command=command)


def test_contract_export_and_negative_fixtures(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    from jsonschema import Draft202012Validator, FormatChecker

    script = Path(__file__).parents[2] / "scripts/export_core_decision_contract.py"
    result = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(tmp_path)], capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode()
    schema = json.loads((tmp_path / "event.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for path in tmp_path.glob("valid-*.json"):
        validator.validate(json.loads(path.read_text()))
        CoreDecisionEvent.model_validate_json(path.read_text())
    invalid = list(tmp_path.glob("invalid-*.json"))
    assert len(invalid) >= 6
    for path in invalid:
        assert list(validator.iter_errors(json.loads(path.read_text()))), path.name
        with pytest.raises(ValidationError):
            CoreDecisionEvent.model_validate_json(path.read_text())
    (tmp_path / "event.schema.json").write_text("{}")
    drift = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(tmp_path), "--check"], capture_output=True
    )
    assert drift.returncode == 1


def test_concurrent_identical_retry_is_one_event(tmp_path):
    path = tmp_path / "p.db"
    CoreDecisionProducer(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        events = list(pool.map(lambda _: publish(CoreDecisionProducer(path)), range(8)))
    assert len({e.message_id for e in events}) == 1
    assert len(CoreDecisionProducer(path).pending(context())) == 1


def test_same_source_across_jobs_does_not_reuse_w1_decision_revision(tmp_path):
    producer = CoreDecisionProducer(tmp_path / "p.db")
    first = publish(producer)
    other_job = context(job_id="10000000-0000-4000-8000-000000000002")
    second = publish(producer, requested=other_job, current=other_job)
    assert (first.decision_version, second.decision_version) == (1, 2)
    assert producer.pending(context()) == [first]
    assert producer.pending(other_job) == [second]


@pytest.mark.parametrize("path", ["", ":memory:"])
def test_rejects_non_durable_database(path):
    with pytest.raises(ValueError, match="DURABLE_PATH_REQUIRED"):
        CoreDecisionProducer(path)
