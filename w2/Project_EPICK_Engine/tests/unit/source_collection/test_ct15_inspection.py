"""Strict, count-only CT15 joint inspection scope tests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator

from epick_engine.source_collection import ct15_inspection
from epick_engine.source_collection.ct15_inspection import (
    MAX_RUN_SCOPE_BYTES,
    Ct15InspectionError,
    inspect_run_counts,
    load_run_scope,
    parse_run_scope,
)


def _raw_scope(*, primary_command: UUID | None = None, secondary_command: UUID | None = None):
    return {
        "schema_version": "w2.ct15.run-inspection.scope.v1",
        "run_id": "ct15-synthetic-joint-1",
        "source_id": str(uuid4()),
        "primary": {
            "owner_ref": str(uuid4()),
            "job_id": str(uuid4()),
            "command_id": str(primary_command or uuid4()),
        },
        "secondary": {
            "owner_ref": str(uuid4()),
            "job_id": str(uuid4()),
            "command_id": str(secondary_command or uuid4()),
        },
    }


def test_parse_run_scope_accepts_only_explicit_synthetic_shape() -> None:
    raw = _raw_scope()
    scope = parse_run_scope(raw)
    assert scope.run_id == "ct15-synthetic-joint-1"
    assert scope.source_id == UUID(raw["source_id"])

    for mutation in [
        {"run_id": "production-run"},
        {"run_id": "ct15-UPPER"},
        {"unexpected_private_body": "PRIVATE-CANARY"},
    ]:
        malformed = dict(raw)
        malformed.update(mutation)
        with pytest.raises(
            Ct15InspectionError, match="^invalid CT15 run inspection scope$"
        ) as caught:
            parse_run_scope(malformed)
        assert "PRIVATE-CANARY" not in str(caught.value)


def test_run_id_matches_w1_exact_boundaries_and_schema() -> None:
    schema_path = (
        Path(__file__).resolve().parents[3] / "contracts/w2-private/ct15-run-inspection.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    scope_schema = dict(schema)
    scope_schema["$ref"] = "#/$defs/scope"
    validator = Draft202012Validator(scope_schema)

    for run_id in ("ct15-a", "ct15-" + "a" * 63):
        raw = _raw_scope()
        raw["run_id"] = run_id
        assert parse_run_scope(raw).run_id == run_id
        validator.validate(raw)
    assert ct15_inspection.CT15_RUN_ID_PATTERN == r"^ct15-[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$"

    excluded = _raw_scope()
    excluded["run_id"] = "ct15-ab"
    with pytest.raises(Ct15InspectionError, match="^invalid CT15 run inspection scope$"):
        parse_run_scope(excluded)
    assert list(validator.iter_errors(excluded))


def test_parse_and_inspect_revalidate_bypassed_model_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = parse_run_scope(_raw_scope())
    bypassed = scope.model_copy(update={"run_id": "production-private-canary"})
    with pytest.raises(Ct15InspectionError, match="^invalid CT15 run inspection scope$"):
        parse_run_scope(bypassed)

    locked = []
    monkeypatch.setattr(ct15_inspection, "_lock_command", lambda *_args: locked.append(True))
    with pytest.raises(Ct15InspectionError, match="^invalid CT15 run inspection scope$"):
        inspect_run_counts(object(), bypassed)
    assert locked == []


@pytest.mark.parametrize("field", ["owner_ref", "job_id", "command_id"])
def test_parse_run_scope_rejects_primary_secondary_identity_collisions(field: str) -> None:
    raw = _raw_scope()
    raw["secondary"][field] = raw["primary"][field]
    with pytest.raises(Ct15InspectionError, match="^invalid CT15 run inspection scope$"):
        parse_run_scope(raw)


def test_load_run_scope_is_size_bounded_and_never_leaks_invalid_fixture(
    tmp_path: Path,
) -> None:
    valid_path = tmp_path / "scope.json"
    valid_path.write_text(json.dumps(_raw_scope()), encoding="utf-8")
    assert load_run_scope(valid_path).run_id == "ct15-synthetic-joint-1"

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text('{"run_id":"ct15-a","private":"PRIVATE-CANARY"}', encoding="utf-8")
    with pytest.raises(Ct15InspectionError) as caught:
        load_run_scope(invalid_path)
    assert str(caught.value) == "invalid CT15 run inspection scope"
    assert "PRIVATE-CANARY" not in str(caught.value)

    oversized_path = tmp_path / "oversized.json"
    oversized_path.write_bytes(b"x" * (MAX_RUN_SCOPE_BYTES + 1))
    with pytest.raises(Ct15InspectionError, match="^CT15 run inspection scope exceeds bound$"):
        load_run_scope(oversized_path)


def test_load_run_scope_reads_at_most_bound_plus_one_without_stat_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "growing.json"
    path.write_text("{}", encoding="utf-8")
    requested: list[int] = []

    class GrowingReader:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int = -1) -> bytes:
            requested.append(size)
            return b"PRIVATE-CANARY" + b"x" * MAX_RUN_SCOPE_BYTES

    original_open = Path.open
    original_stat = Path.stat

    def controlled_open(self, mode="r", *args, **kwargs):
        if self == path:
            assert mode == "rb"
            return GrowingReader()
        return original_open(self, mode, *args, **kwargs)

    def forbidden_stat(self, *args, **kwargs):
        if self == path:
            raise AssertionError("stat-before-read is race-prone")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", controlled_open)
    monkeypatch.setattr(Path, "stat", forbidden_stat)
    with pytest.raises(Ct15InspectionError) as caught:
        load_run_scope(path)
    assert str(caught.value) == "CT15 run inspection scope exceeds bound"
    assert "PRIVATE-CANARY" not in str(caught.value)
    assert requested == [MAX_RUN_SCOPE_BYTES + 1]


def test_inspect_run_counts_locks_both_commands_in_uuid_order_before_missing_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lower, higher = UUID(int=1), UUID(int=2)
    scope = parse_run_scope(_raw_scope(primary_command=higher, secondary_command=lower))
    locked: list[UUID] = []

    class MissingSession:
        @staticmethod
        def get(_model, _key, **_kwargs):
            return None

    monkeypatch.setattr(ct15_inspection, "_lock_command", lambda _session, key: locked.append(key))
    result = inspect_run_counts(MissingSession(), scope)

    assert locked == [lower, higher]
    assert result["counts"]["primary"] == result["counts"]["secondary"]
    assert result["counts"]["total"]["ack_count"] == 0
    assert sum(result["counts"]["total"]["state_counts"].values()) == 0
    assert result["source_verification"] == {"primary": "absent", "secondary": "absent"}
