from __future__ import annotations

from pathlib import Path

import pytest

from app.clients.engine_capabilities import (
    CapabilityFailureCode,
    EngineCapabilityProfile,
    ServiceCapabilityRequirement,
    evaluate_capability,
    load_contract_fixture,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "engine_contract_v1.json"


@pytest.fixture
def engine_contract_fixture() -> dict[str, object]:
    return load_contract_fixture(FIXTURE_PATH)


def test_supported_v1_engine_contract_is_compatible(
    engine_contract_fixture: dict[str, object],
) -> None:
    requirement = ServiceCapabilityRequirement.model_validate(
        engine_contract_fixture["service_requirement"]
    )
    capability = EngineCapabilityProfile.model_validate(
        engine_contract_fixture["compatible_engine"]
    )

    result = evaluate_capability(requirement, capability)

    assert result.compatible is True
    assert result.code is None
    assert result.reason is None


@pytest.mark.parametrize("profile_index", [0, 1])
def test_incompatible_contract_or_schema_fails_closed_before_invocation(
    engine_contract_fixture: dict[str, object], profile_index: int
) -> None:
    requirement = ServiceCapabilityRequirement.model_validate(
        engine_contract_fixture["service_requirement"]
    )
    incompatible_profiles = engine_contract_fixture["incompatible_engines"]
    assert isinstance(incompatible_profiles, list)
    capability = EngineCapabilityProfile.model_validate(incompatible_profiles[profile_index])

    result = evaluate_capability(requirement, capability)

    assert result.compatible is False
    assert result.code is CapabilityFailureCode.CONTRACT_INCOMPATIBLE
    assert result.reason is not None
    assert "unsupported" in result.reason
