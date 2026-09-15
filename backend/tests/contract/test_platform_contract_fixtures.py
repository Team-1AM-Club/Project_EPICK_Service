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
            "fixtures/v1/w2/source-collection-command.json",
        ),
        (
            "w2/v1/source-collection.result.schema.json",
            "fixtures/v1/w2/source-collection-result-complete.json",
        ),
        (
            "w2/v1/source-collection.result.schema.json",
            "fixtures/v1/w2/source-collection-result-partial.json",
        ),
        (
            "w2/v1/source-collection.result.schema.json",
            "fixtures/v1/w2/source-collection-result-failure.json",
        ),
    ],
)
def test_versioned_contract_fixtures_match_their_schema(
    contract_root: Path, schema_path: str, fixture_path: str
) -> None:
    validator = _validator(contract_root, schema_path)

    assert list(validator.iter_errors(_load(contract_root / fixture_path))) == []


@pytest.mark.parametrize(
    "fixture_path",
    [
        "fixtures/v1/w2/source-event-version-available.json",
        "fixtures/v1/w2/source-event-version-partial.json",
        "fixtures/v1/w2/source-event-observation-changed.json",
        "fixtures/v1/w2/source-event-restriction-changed.json",
    ],
)
def test_w2_public_source_events_match_the_common_envelope_and_w2_payload(
    contract_root: Path, fixture_path: str
) -> None:
    event = _load(contract_root / fixture_path)
    envelope_validator = _validator(contract_root, "common/v1/event-envelope.schema.json")
    payload_validator = _validator(contract_root, "w2/v1/source-event-payload.schema.json")

    assert list(envelope_validator.iter_errors(event)) == []
    assert list(payload_validator.iter_errors(event["payload"])) == []


@pytest.mark.parametrize(
    ("schema_path", "fixture_path"),
    [
        (
            "common/v1/event-envelope.schema.json",
            "fixtures/v1/common/invalid-public-private-field.json",
        ),
        ("w1/v1/job.schema.json", "fixtures/v1/w1/invalid-job-status.json"),
    ],
)
def test_prohibited_or_unknown_contract_values_are_rejected(
    contract_root: Path, schema_path: str, fixture_path: str
) -> None:
    validator = _validator(contract_root, schema_path)

    assert list(validator.iter_errors(_load(contract_root / fixture_path)))


@pytest.mark.parametrize(
    "fixture_path",
    [
        "fixtures/v1/w2/invalid-source-event-private-field.json",
        "fixtures/v1/w2/invalid-source-event-transport-fields.json",
    ],
)
def test_w2_public_source_events_reject_private_or_transport_fields(
    contract_root: Path, fixture_path: str
) -> None:
    event = _load(contract_root / fixture_path)
    envelope_validator = _validator(contract_root, "common/v1/event-envelope.schema.json")
    payload_validator = _validator(contract_root, "w2/v1/source-event-payload.schema.json")

    assert list(envelope_validator.iter_errors(event)) or list(
        payload_validator.iter_errors(event["payload"])
    )


def test_w2_import_is_pinned_to_the_observed_runtime_contract(contract_root: Path) -> None:
    manifest = _load(contract_root / "w2/v1/import-manifest.json")

    assert manifest["contract_status"] == (
        "PRIVATE_CONTRACT_ADOPTED_PUBLIC_SOURCE_EVENT_COMPATIBILITY_PENDING"
    )
    assert manifest["declared_runtime_schema_versions"] == ["w2.collection.v1", "w2.source.v1"]
    assert manifest["artifact_authority"] == "W2"
    assert manifest["source_commit"] is None
    assert manifest["source_commit_status"] == "NOT_PROVIDED_WITH_W2_ARTIFACT_PACKAGE"
    assert set(manifest["artifacts"]) == {
        "source-collection.command.schema.json",
        "source-collection.result.schema.json",
        "source-event-payload.schema.json",
    }
    for name, artifact in manifest["artifacts"].items():
        actual_sha256 = hashlib.sha256((contract_root / "w2/v1" / name).read_bytes()).hexdigest()
        assert actual_sha256 == artifact["sha256"]

    fixture_root = contract_root / "fixtures/v1/w2"
    for name, expected_sha256 in manifest["fixtures"].items():
        actual_sha256 = hashlib.sha256((fixture_root / name).read_bytes()).hexdigest()
        assert actual_sha256 == expected_sha256
