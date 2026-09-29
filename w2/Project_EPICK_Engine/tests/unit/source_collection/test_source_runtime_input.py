from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]
from pydantic import ValidationError
from sqlalchemy.orm import Session

from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionStage,
    CoreSourceDecision,
    Permission,
)
from epick_engine.source_collection.policy import ExecutionLimits
from epick_engine.source_collection.source_runtime_input import (
    RuntimeSourceConfigFile,
    SourceRuntimeInputError,
    SqlAlchemyCollectionInputProvider,
    parse_runtime_source_config_json,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ID = UUID("11111111-1111-4111-8111-111111111111")
COMPANY_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _limits() -> dict[str, int | float]:
    return {
        "site_concurrency": 1,
        "global_concurrency": 2,
        "source_ttl_seconds": 300,
        "max_response_bytes": 1_048_576,
        "max_decompressed_bytes": 2_097_152,
        "connect_timeout_seconds": 3.0,
        "read_timeout_seconds": 5.0,
        "max_redirects": 2,
        "general_retry_limit": 0,
        "retention_days": 7,
    }


def _source_config() -> dict[str, object]:
    return {
        "policy_revision": 3,
        "robots_permission": "allowed",
        "result_version": 1,
        "language": "ko",
        "redirect_robots_permissions": [],
        "limits": _limits(),
    }


def _config_payload() -> dict[str, object]:
    return {
        "schema_version": "w2.source-runtime-config.v1",
        "claim_lease_seconds": 120,
        "sources": {str(SOURCE_ID): _source_config()},
    }


def _parse(payload: dict[str, object]) -> RuntimeSourceConfigFile:
    return parse_runtime_source_config_json(json.dumps(payload))


def _schema_validator() -> Draft202012Validator:
    schema_path = PROJECT_ROOT / "contracts" / "w2-private" / "source-runtime-config.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _wire_schema_accepts(payload: dict[str, object]) -> bool:
    try:
        wire_value = json.loads(json.dumps(payload, allow_nan=False))
    except ValueError:
        return False
    return bool(_schema_validator().is_valid(wire_value))


def _wire_model_accepts(payload: dict[str, object]) -> bool:
    try:
        wire_json = json.dumps(payload, allow_nan=False)
        parse_runtime_source_config_json(wire_json)
    except (TypeError, ValueError):
        return False
    return True


def _replace_payload_value(
    payload: dict[str, object],
    path: tuple[str, ...],
    value: object,
) -> None:
    target: dict[str, Any] = payload
    for component in path[:-1]:
        nested = target[component]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value


def _unexpected_session_factory() -> Session:
    raise AssertionError("session must not be opened")


def _command(
    *,
    source_id: UUID = SOURCE_ID,
    company_id: UUID = COMPANY_ID,
    resume_stage: CollectionStage = CollectionStage.POLICY,
    policy_revision: int | None = None,
) -> CollectionCommand:
    return CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=uuid4(),
        job_id=uuid4(),
        authenticated_owner_ref=uuid4(),
        project_ref=None,
        company_id=company_id,
        source_id=source_id,
        input_version=3,
        execution_fence="7",
        purpose_ref=uuid4(),
        core_source_decision=CoreSourceDecision(
            is_core=True,
            decided_by="W3",
            rationale="CORE_REQUIRED",
            decision_revision=3,
            analysis_input_version=3,
        ),
        resume_stage=resume_stage,
        policy_revision=policy_revision,
        owner_deletion_epoch=0,
    )


def test_runtime_config_strictly_parses_and_normalizes_the_complete_example() -> None:
    payload = _config_payload()
    sources = payload["sources"]
    assert isinstance(sources, dict)
    source_payload = sources[str(SOURCE_ID)]
    assert isinstance(source_payload, dict)
    source_payload["redirect_robots_permissions"] = [
        ["https://careers.example.test/jobs", "denied"],
    ]
    config = _parse(payload)

    assert config.schema_version == "w2.source-runtime-config.v1"
    assert set(config.sources) == {SOURCE_ID}
    source = config.sources[SOURCE_ID]
    assert source.policy_revision == 3
    assert source.robots_permission is Permission.ALLOWED
    assert source.execution_limits == ExecutionLimits(
        site_concurrency=1,
        global_concurrency=2,
        source_ttl_seconds=300,
        max_response_bytes=1_048_576,
        max_decompressed_bytes=2_097_152,
        connect_timeout_seconds=3.0,
        read_timeout_seconds=5.0,
        max_redirects=2,
        general_retry_limit=0,
        retention_days=7,
    )
    assert source.redirect_robots_permissions == (
        ("https://careers.example.test/jobs", Permission.DENIED),
    )
    assert config.heartbeat_interval_seconds == 40.0
    assert config.heartbeat_interval_seconds <= config.claim_lease_seconds / 3


def test_runtime_config_schema_accepts_the_complete_example() -> None:
    validator = _schema_validator()
    schema = validator.schema
    Draft202012Validator.check_schema(schema)

    errors = list(validator.iter_errors(_config_payload()))

    assert errors == []


def test_runtime_config_is_detached_from_input_and_serializes_normalized_sources() -> None:
    payload = _config_payload()
    raw_sources = payload["sources"]
    assert isinstance(raw_sources, dict)
    raw_source = raw_sources[str(SOURCE_ID)]
    assert isinstance(raw_source, dict)

    config = RuntimeSourceConfigFile.model_validate(payload)
    raw_source["policy_revision"] = 99
    raw_sources.clear()

    assert set(config.sources) == {SOURCE_ID}
    assert config.sources[SOURCE_ID].policy_revision == 3
    dumped = config.model_dump(mode="json")
    assert dumped["sources"][str(SOURCE_ID)]["policy_revision"] == 3


@pytest.mark.parametrize("operation", ["add", "delete", "replace"])
def test_runtime_config_source_mapping_rejects_post_parse_mutation(operation: str) -> None:
    config = _parse(_config_payload())
    mutable_sources: Any = config.sources

    with pytest.raises(TypeError):
        if operation == "add":
            mutable_sources[uuid4()] = config.sources[SOURCE_ID]
        elif operation == "delete":
            del mutable_sources[SOURCE_ID]
        else:
            mutable_sources[SOURCE_ID] = config.sources[SOURCE_ID].model_copy(
                update={"policy_revision": 99}
            )

    assert set(config.sources) == {SOURCE_ID}
    assert config.sources[SOURCE_ID].policy_revision == 3


@pytest.mark.parametrize(
    ("path", "value", "accepted"),
    [
        (("sources", str(SOURCE_ID), "limits", "connect_timeout_seconds"), 3, True),
        (("sources", str(SOURCE_ID), "limits", "read_timeout_seconds"), 5, True),
        (("sources", str(SOURCE_ID), "limits", "connect_timeout_seconds"), 0, False),
        (("sources", str(SOURCE_ID), "limits", "connect_timeout_seconds"), -1, False),
        (("sources", str(SOURCE_ID), "limits", "connect_timeout_seconds"), True, False),
        (("sources", str(SOURCE_ID), "limits", "connect_timeout_seconds"), math.inf, False),
        (("sources", str(SOURCE_ID), "limits", "read_timeout_seconds"), math.nan, False),
        (("sources", str(SOURCE_ID), "limits", "read_timeout_seconds"), "5", False),
        (("sources", str(SOURCE_ID), "limits", "general_retry_limit"), 0, True),
        (("sources", str(SOURCE_ID), "limits", "general_retry_limit"), -1, False),
        (("sources", str(SOURCE_ID), "limits", "max_redirects"), 0, False),
    ],
)
def test_schema_and_model_share_numeric_wire_acceptance(
    path: tuple[str, ...],
    value: object,
    accepted: bool,
) -> None:
    payload = deepcopy(_config_payload())
    _replace_payload_value(payload, path, value)

    assert _wire_schema_accepts(payload) is accepted
    assert _wire_model_accepts(payload) is accepted


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("claim_lease_seconds",), 120.0),
        (("sources", str(SOURCE_ID), "policy_revision"), 3.0),
        (("sources", str(SOURCE_ID), "result_version"), 1.0),
        (("sources", str(SOURCE_ID), "limits", "site_concurrency"), 1.0),
        (("sources", str(SOURCE_ID), "limits", "global_concurrency"), 2.0),
        (("sources", str(SOURCE_ID), "limits", "source_ttl_seconds"), 300.0),
        (("sources", str(SOURCE_ID), "limits", "max_response_bytes"), 1_048_576.0),
        (
            ("sources", str(SOURCE_ID), "limits", "max_decompressed_bytes"),
            2_097_152.0,
        ),
        (("sources", str(SOURCE_ID), "limits", "max_redirects"), 2.0),
        (("sources", str(SOURCE_ID), "limits", "general_retry_limit"), 0.0),
        (("sources", str(SOURCE_ID), "limits", "retention_days"), 7.0),
    ],
)
def test_schema_and_authoritative_parser_accept_integral_json_numbers_for_integer_fields(
    path: tuple[str, ...],
    value: float,
) -> None:
    payload = deepcopy(_config_payload())
    _replace_payload_value(payload, path, value)

    assert _wire_schema_accepts(payload) is True
    assert _wire_model_accepts(payload) is True


def test_authoritative_parser_accepts_integral_exponent_for_an_integer_field() -> None:
    raw_json = json.dumps(_config_payload()).replace(
        '"result_version": 1,',
        '"result_version": 1e0,',
        1,
    )
    assert '"result_version": 1e0,' in raw_json
    assert _schema_validator().is_valid(json.loads(raw_json)) is True

    config = parse_runtime_source_config_json(raw_json)

    assert config.sources[SOURCE_ID].result_version == 1


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("sources", str(SOURCE_ID), "policy_revision"), 3.5),
        (("sources", str(SOURCE_ID), "result_version"), True),
        (("sources", str(SOURCE_ID), "limits", "site_concurrency"), math.inf),
        (("sources", str(SOURCE_ID), "limits", "retention_days"), math.nan),
    ],
)
def test_schema_and_authoritative_parser_reject_invalid_integer_numbers(
    path: tuple[str, ...],
    value: object,
) -> None:
    payload = deepcopy(_config_payload())
    _replace_payload_value(payload, path, value)

    assert _wire_schema_accepts(payload) is False
    assert _wire_model_accepts(payload) is False


def test_python_object_model_validation_keeps_strict_integer_fields() -> None:
    payload = _config_payload()
    _replace_payload_value(payload, ("sources", str(SOURCE_ID), "result_version"), 1.0)

    with pytest.raises(ValidationError):
        RuntimeSourceConfigFile.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ((), "unexpected_root"),
        (("sources", str(SOURCE_ID)), "unexpected_source"),
        (("sources", str(SOURCE_ID), "limits"), "unexpected_limit"),
    ],
)
def test_schema_and_model_both_reject_unknown_keys(
    path: tuple[str, ...],
    field: str,
) -> None:
    payload = deepcopy(_config_payload())
    target: dict[str, Any] = payload
    for component in path:
        nested = target[component]
        assert isinstance(nested, dict)
        target = nested
    target[field] = 1

    assert _wire_schema_accepts(payload) is False
    assert _wire_model_accepts(payload) is False


def test_duplicate_normalized_uuid_keys_are_rejected_at_the_model_boundary() -> None:
    payload = _config_payload()
    duplicate_source_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    payload["sources"] = {
        str(duplicate_source_id): _source_config(),
        str(duplicate_source_id).upper(): _source_config(),
    }

    assert _wire_schema_accepts(payload) is True
    assert _wire_model_accepts(payload) is False


def test_authoritative_parser_rejects_an_identical_repeated_raw_json_key() -> None:
    source_json = json.dumps(_source_config())
    source_id = str(SOURCE_ID)
    raw_json = (
        '{"schema_version":"w2.source-runtime-config.v1",'
        '"claim_lease_seconds":120,"sources":{'
        f'"{source_id}":{source_json},"{source_id}":{source_json}'
        "}}"
    )

    with pytest.raises(SourceRuntimeInputError, match="duplicate JSON object key"):
        parse_runtime_source_config_json(raw_json)


def test_schema_shape_is_not_authoritative_for_cross_field_lease_validation() -> None:
    payload = _config_payload()
    payload["claim_lease_seconds"] = 17

    assert _wire_schema_accepts(payload) is True
    with pytest.raises(ValidationError, match="claim lease"):
        _parse(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "w2.source-runtime-config.v2"),
        (("claim_lease_seconds",), 0),
        (("claim_lease_seconds",), -1),
        (("claim_lease_seconds",), True),
        (("sources", str(SOURCE_ID), "policy_revision"), True),
        (("sources", str(SOURCE_ID), "result_version"), 0),
        (("sources", str(SOURCE_ID), "limits", "general_retry_limit"), -1),
        (("sources", str(SOURCE_ID), "limits", "connect_timeout_seconds"), math.inf),
        (("sources", str(SOURCE_ID), "limits", "read_timeout_seconds"), math.nan),
    ],
)
def test_runtime_config_rejects_wrong_versions_and_non_strict_numeric_values(
    path: tuple[str, ...],
    value: object,
) -> None:
    payload = _config_payload()
    target: dict[str, Any] = payload
    for component in path[:-1]:
        nested = target[component]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        RuntimeSourceConfigFile.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ((), "password"),
        (("sources", str(SOURCE_ID)), "api_key"),
        (("sources", str(SOURCE_ID), "limits"), "burst_limit"),
        ((), "heartbeat_interval_seconds"),
    ],
)
def test_runtime_config_rejects_unknown_secret_and_tunable_heartbeat_fields(
    path: tuple[str, ...],
    field: str,
) -> None:
    payload = _config_payload()
    target: dict[str, Any] = payload
    for component in path:
        nested = target[component]
        assert isinstance(nested, dict)
        target = nested
    target[field] = "not-allowed"

    with pytest.raises(ValidationError):
        RuntimeSourceConfigFile.model_validate(payload)


def test_runtime_config_rejects_duplicate_source_after_uuid_normalization() -> None:
    payload = _config_payload()
    duplicate_source_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    payload["sources"] = {
        str(duplicate_source_id): _source_config(),
        str(duplicate_source_id).upper(): _source_config(),
    }

    with pytest.raises(ValidationError, match="duplicate Source"):
        _parse(payload)


def test_claim_lease_must_exceed_fetch_deadline_and_both_socket_timeouts() -> None:
    payload = _config_payload()
    # Existing collector deadline is 3 * (0 + 1) * (2 + 1) = 9 seconds;
    # adding connect/read timeouts yields a strict lower bound of 17 seconds.
    payload["claim_lease_seconds"] = 17

    with pytest.raises(ValidationError, match="claim lease"):
        _parse(payload)

    payload["claim_lease_seconds"] = 18
    assert _parse(payload).claim_lease_seconds == 18


@pytest.mark.parametrize(
    ("resume_stage", "policy_revision"),
    [
        (CollectionStage.POLICY, 3),
        (CollectionStage.FETCH, 3),
    ],
)
def test_provider_rejects_non_initial_commands_before_opening_a_session(
    resume_stage: CollectionStage,
    policy_revision: int,
) -> None:
    opened = False

    def session_factory() -> Session:
        nonlocal opened
        opened = True
        raise AssertionError("session must not be opened")

    provider = SqlAlchemyCollectionInputProvider(session_factory, _parse(_config_payload()))

    with pytest.raises(SourceRuntimeInputError, match="initial policy command"):
        provider.load(_command(resume_stage=resume_stage, policy_revision=policy_revision))

    assert opened is False


def test_provider_rejects_missing_source_config_before_opening_a_session() -> None:
    opened = False

    def session_factory() -> Session:
        nonlocal opened
        opened = True
        raise AssertionError("session must not be opened")

    provider = SqlAlchemyCollectionInputProvider(session_factory, _parse(_config_payload()))

    with pytest.raises(SourceRuntimeInputError, match="approved source config"):
        provider.load(_command(source_id=uuid4()))

    assert opened is False


def test_provider_exposes_non_configurable_claim_and_heartbeat_timing() -> None:
    provider = SqlAlchemyCollectionInputProvider(
        _unexpected_session_factory,
        _parse(_config_payload()),
    )

    assert provider.claim_lease_seconds == 120
    assert provider.heartbeat_interval_seconds == 40.0


def _approved_site_payload() -> dict[str, object]:
    source_config = _source_config()
    source_config["policy_revision"] = 1
    return {
        "company_id": str(COMPANY_ID),
        "company_legal_name": "Example",
        "company_official_domain": "example.test",
        "hostname": "careers.example.test",
        "path_prefix": "/jobs/",
        "w1_source_type": "JOB_POSTING",
        "w2_source_type": "job_posting",
        "company_identity_evidence": ["synthetic:company"],
        "policy": {
            "official_status": "verified",
            "access_class": "public",
            "collection_permission": "allowed",
            "excerpt_storage_permission": "allowed",
            "body_storage_permission": "denied",
            "redistribution_permission": "denied",
            "evidence_refs": ["synthetic:site-approval"],
            "checked_at": "2026-09-29T00:00:00Z",
            "policy_version": "synthetic-policy-v1",
        },
        "source_config": source_config,
    }


def test_onboarding_requires_explicit_company_site_path_and_policy() -> None:
    payload = _config_payload()
    payload["sources"] = {}
    payload["approved_sites"] = [_approved_site_payload()]
    assert _wire_schema_accepts(payload)
    config = _parse(payload)
    assert config.sources == {}
    approved = config.onboarding_profile(
        company_id=COMPANY_ID,
        url="https://careers.example.test/jobs/123",
        w1_source_type="JOB_POSTING",
    )
    assert approved is not None
    assert approved.source_config.policy_revision == 1
    for url in (
        "https://careers.example.test/jobs-extra/123",
        "https://evil.test/jobs/123",
        "http://careers.example.test/jobs/123",
        "https://user@careers.example.test/jobs/123",
    ):
        assert (
            config.onboarding_profile(
                company_id=COMPANY_ID, url=url, w1_source_type="JOB_POSTING"
            )
            is None
        )
    assert (
        config.onboarding_profile(
            company_id=uuid4(),
            url="https://careers.example.test/jobs/123",
            w1_source_type="JOB_POSTING",
        )
        is None
    )


def test_onboarding_rejects_missing_evidence_and_duplicate_profile() -> None:
    payload = _config_payload()
    approved = _approved_site_payload()
    payload["approved_sites"] = [approved, deepcopy(approved)]
    with pytest.raises(ValidationError, match="duplicate onboarding approval"):
        _parse(payload)
    policy = approved["policy"]
    assert isinstance(policy, dict)
    policy["evidence_refs"] = []
    payload["approved_sites"] = [approved]
    with pytest.raises(ValidationError):
        _parse(payload)


def test_runtime_config_rejects_unapproved_empty_source_set() -> None:
    payload = _config_payload()
    payload["sources"] = {}
    assert not _wire_schema_accepts(payload)
    with pytest.raises(ValidationError, match="at least one pinned Source"):
        _parse(payload)
