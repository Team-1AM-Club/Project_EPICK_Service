from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


def _validator(contract_root: Path, relative_schema_path: str) -> Draft202012Validator:
    schema = _load(contract_root / relative_schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize(
    ("schema_path", "fixture_path"),
    [
        (
            "common/v1/event-envelope.schema.json",
            "fixtures/v1/common/source-restriction-changed.json",
        ),
        (
            "common/v1/event-envelope.schema.json",
            "fixtures/v1/common/source-restriction-replay.json",
        ),
        (
            "common/v1/event-envelope.schema.json",
            "fixtures/v1/common/source-revision-gap.json",
        ),
        ("w1/v1/job.schema.json", "fixtures/v1/w1/accepted-job.json"),
        ("w1/v1/job.schema.json", "fixtures/v1/w1/cancel-requested-job.json"),
        ("w1/v1/job-command.schema.json", "fixtures/v1/w1/job-command.json"),
        ("w1/v1/checkpoint.schema.json", "fixtures/v1/w1/checkpoint.json"),
        ("w1/v1/deletion-command.schema.json", "fixtures/v1/w1/deletion-command.json"),
        (
            "w2/v1/source-collection.command.schema.json",
            "fixtures/v1/w2/initial-policy-command.json",
        ),
        (
            "w2/v1/source-collection.command.schema.json",
            "fixtures/v1/w2/resumed-command.json",
        ),
        (
            "w2/v1/source-collection.result.schema.json",
            "fixtures/v1/w2/complete-result.json",
        ),
        (
            "w2/v1/source-collection.result.schema.json",
            "fixtures/v1/w2/partial-result.json",
        ),
        (
            "w2/v1/source-collection.result.schema.json",
            "fixtures/v1/w2/policy-failure-result.json",
        ),
        ("w2/v1/source-event.schema.json", "fixtures/v1/w2/source-version-available.json"),
        ("w2/v1/source-event.schema.json", "fixtures/v1/w2/source-observation-changed.json"),
        (
            "w2/v1/source-event.schema.json",
            "fixtures/v1/w2/source-restriction-changed.json",
        ),
    ],
)
def test_versioned_contract_fixtures_match_their_schema(
    contract_root: Path, schema_path: str, fixture_path: str
) -> None:
    validator = _validator(contract_root, schema_path)

    assert list(validator.iter_errors(_load(contract_root / fixture_path))) == []


@pytest.mark.parametrize(
    ("schema_path", "fixture_path"),
    [
        (
            "common/v1/event-envelope.schema.json",
            "fixtures/v1/common/invalid-public-private-field.json",
        ),
        ("w1/v1/job.schema.json", "fixtures/v1/w1/invalid-job-status.json"),
        ("w2/v1/source-event.schema.json", "fixtures/v1/w2/invalid-public-private-field.json"),
        ("w2/v1/source-event.schema.json", "fixtures/v1/w2/invalid-event-payload-kind.json"),
    ],
)
def test_prohibited_or_unknown_contract_values_are_rejected(
    contract_root: Path, schema_path: str, fixture_path: str
) -> None:
    validator = _validator(contract_root, schema_path)

    assert list(validator.iter_errors(_load(contract_root / fixture_path)))


def test_w2_import_is_pinned_to_the_observed_runtime_contract(contract_root: Path) -> None:
    manifest = _load(contract_root / "w2/v1/import-manifest.json")

    assert manifest["contract_status"] == (
        "PRIVATE_CONTRACT_ADOPTED_PUBLIC_SOURCE_EVENT_COMPATIBILITY_PENDING"
    )
    assert manifest["declared_runtime_schema_versions"] == ["w2.collection.v1", "w2.source.v1"]
    assert manifest["source_commit"] == "0865ecdfe4748dad5679bc82b9f7386dc663675e"
    assert set(manifest["artifacts"]) == {
        "source-collection.command.schema.json",
        "source-collection.result.schema.json",
        "source-event.schema.json",
    }
    for name, artifact in manifest["artifacts"].items():
        actual_sha256 = hashlib.sha256((contract_root / "w2/v1" / name).read_bytes()).hexdigest()
        assert actual_sha256 == artifact["sha256"]

    fixture_root = contract_root / "fixtures/v1/w2"
    for name, expected_sha256 in manifest["fixtures"].items():
        actual_sha256 = hashlib.sha256((fixture_root / name).read_bytes()).hexdigest()
        assert actual_sha256 == expected_sha256
