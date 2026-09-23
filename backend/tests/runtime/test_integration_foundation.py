"""Fail-closed shared primitives for the W1→W2→W3→W4 chain."""

from __future__ import annotations

import hashlib
import importlib
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.runtime.integration_context import IntegrationContext, IntegrationHop
from app.runtime.integration_logging import safe_integration_log
from app.runtime.integration_preflight import (
    CONTRACT_NAMES,
    CONTRACT_ROOT,
    IntegrationPreflightError,
    validate_integration_startup,
)
from app.runtime.integration_retry import (
    PrivateCallTimeouts,
    RetryDecision,
    RetryPolicy,
    classify_http_status,
    classify_queue_failure,
)


def _context() -> IntegrationContext:
    return IntegrationContext(
        run_id=uuid4(),
        job_id=uuid4(),
        source_id=uuid4(),
        analysis_request_id=uuid4(),
        recommendation_run_id=uuid4(),
        execution_fence=2,
        owner_deletion_epoch=1,
        analysis_input_version="input-v2",
        idempotency_key="run-input-v2",
        correlation_id=uuid4(),
    )


def test_context_keeps_currentness_when_propagated_to_next_hop() -> None:
    original = _context()
    next_hop = original.for_hop("w2-collection")

    assert next_hop.context == original
    assert next_hop.hop == "w2-collection"
    assert next_hop.currentness == (2, 1, "input-v2")
    assert next_hop.context.idempotency_key == "run-input-v2"


@pytest.mark.parametrize("fence,epoch", [(0, 0), (1, -1)])
def test_context_rejects_invalid_currentness(fence: int, epoch: int) -> None:
    with pytest.raises(ValueError):
        IntegrationContext(
            run_id=uuid4(),
            job_id=uuid4(),
            execution_fence=fence,
            owner_deletion_epoch=epoch,
            analysis_input_version="input-v1",
            idempotency_key="test-key",
            correlation_id=uuid4(),
        )


def test_context_rejects_non_uuid_identifier_before_logging() -> None:
    with pytest.raises(ValueError, match="UUID"):
        IntegrationContext(
            run_id="owner=private",  # type: ignore[arg-type]
            job_id=uuid4(),
            execution_fence=1,
            owner_deletion_epoch=0,
            analysis_input_version="input-v1",
            idempotency_key="test-key",
            correlation_id=uuid4(),
        )


def test_hop_rejects_arbitrary_private_label_even_when_constructed_directly() -> None:
    with pytest.raises(ValueError, match="hop"):
        IntegrationHop(context=_context(), hop="postgresql://private")


def test_structured_log_exposes_only_sanitized_identifiers_and_status() -> None:
    context = _context()
    log = safe_integration_log(context.for_hop("w3-core"), event="dispatch", status="queued")

    assert log == {
        "correlation_id": str(context.correlation_id),
        "run_id": str(context.run_id),
        "hop": "w3-core",
        "event": "dispatch",
        "status": "queued",
    }
    assert str(context.job_id) not in str(log)
    assert str(context.source_id) not in str(log)


def test_structured_log_rejects_arbitrary_private_fields() -> None:
    with pytest.raises(TypeError):
        safe_integration_log(
            _context().for_hop("w2-collection"),
            event="dispatch",
            status="queued",
            database_url="postgresql://private",
        )


@pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
def test_http_transient_statuses_are_retryable(status: int) -> None:
    assert classify_http_status(status) is RetryDecision.RETRY


@pytest.mark.parametrize("status", [200, 202, 400, 401, 403, 404, 409, 422])
def test_http_success_and_conflict_are_not_retried(status: int) -> None:
    assert classify_http_status(status) is not RetryDecision.RETRY


def test_retry_policy_has_bounded_backoff_and_terminal_attempt() -> None:
    policy = RetryPolicy(max_attempts=4, base_seconds=2, cap_seconds=5)
    assert [policy.delay_seconds(attempt) for attempt in (1, 2, 3)] == [2, 4, 5]
    assert policy.should_retry(RetryDecision.RETRY, attempt=3)
    assert not policy.should_retry(RetryDecision.RETRY, attempt=4)
    assert not policy.should_retry(RetryDecision.TERMINAL, attempt=1)


def test_private_call_timeout_budget_is_bounded() -> None:
    assert PrivateCallTimeouts(connect_seconds=1, read_seconds=2, total_seconds=5)
    with pytest.raises(ValueError):
        PrivateCallTimeouts(connect_seconds=5, read_seconds=2, total_seconds=3)


@pytest.mark.parametrize("code", ["ThrottlingException", "RequestThrottled", "ServiceUnavailable"])
def test_queue_transient_failures_are_retryable(code: str) -> None:
    assert classify_queue_failure(code) is RetryDecision.RETRY


@pytest.mark.parametrize("code", ["AccessDenied", "InvalidAddress", "InvalidMessageContents"])
def test_queue_configuration_and_contract_failures_are_terminal(code: str) -> None:
    assert classify_queue_failure(code) is RetryDecision.TERMINAL


def test_integration_preflight_is_disabled_by_default() -> None:
    assert validate_integration_startup(Settings(_env_file=None)) == {"status": "disabled"}


def test_integration_preflight_rejects_missing_contract_pins() -> None:
    settings = Settings(
        _env_file=None,
        integration_enabled=True,
        integration_w2_private_origin="http://w2",
        integration_w3_private_origin="http://w3",
        integration_w4_private_origin="http://w4",
        integration_policy_revision="local-draft",
    )
    with pytest.raises(IntegrationPreflightError, match="contract pins"):
        validate_integration_startup(settings)


def test_integration_preflight_rejects_real_without_policy_approval() -> None:
    settings = Settings(
        _env_file=None,
        integration_real_data_enabled=True,
    )
    with pytest.raises(IntegrationPreflightError, match="REAL.*policy"):
        validate_integration_startup(settings)


def _integration_pins() -> dict[str, str]:
    return {
        name: hashlib.sha256(
            (CONTRACT_ROOT / name).read_bytes().replace(b"\r\n", b"\n").rstrip(b"\n")
            + b"\n"
        ).hexdigest()
        for name in CONTRACT_NAMES
    }


def _integration_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "integration_enabled": True,
        "integration_w2_private_origin": "http://w2.internal",
        "integration_w3_private_origin": "http://w3.internal",
        "integration_w4_private_origin": "http://w4.internal",
        "integration_policy_revision": "local-draft-v1",
        "integration_contract_pins": _integration_pins(),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_integration_preflight_accepts_complete_synthetic_local_pins() -> None:
    result = validate_integration_startup(_integration_settings())
    assert result == {"status": "ready", "policy_revision": "local-draft-v1"}


def test_integration_preflight_rejects_public_origin() -> None:
    settings = _integration_settings(integration_w3_private_origin="https://example.com")
    with pytest.raises(IntegrationPreflightError, match="private"):
        validate_integration_startup(settings)


def test_integration_preflight_rejects_contract_drift() -> None:
    pins = _integration_pins()
    pins["deployment-manifest.schema.json"] = "0" * 64
    settings = _integration_settings(integration_contract_pins=pins)
    with pytest.raises(IntegrationPreflightError, match="drift"):
        validate_integration_startup(settings)


def test_api_creation_checks_integration_pins_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_main = importlib.import_module("app.main")
    monkeypatch.setattr(api_main, "settings", _integration_settings(integration_contract_pins={}))
    with pytest.raises(IntegrationPreflightError, match="contract pins"):
        api_main.create_app()


def test_relay_creation_checks_integration_pins_before_io(monkeypatch: pytest.MonkeyPatch) -> None:
    relay_script = importlib.import_module("scripts.run_outbox_relay")
    monkeypatch.setattr(
        relay_script, "settings", _integration_settings(integration_contract_pins={})
    )
    with pytest.raises(IntegrationPreflightError, match="contract pins"):
        relay_script._build_relay()
