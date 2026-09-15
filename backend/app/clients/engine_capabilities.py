from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CapabilityFailureCode(str, Enum):
    CONTRACT_INCOMPATIBLE = "CONTRACT_INCOMPATIBLE"


class EngineRoute(str, Enum):
    GRAPH = "GRAPH"
    SKILL_ALIAS = "SKILL_ALIAS"
    VECTOR = "VECTOR"


class ServiceCapabilityRequirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    projection_event_contract_version: str
    projection_dto_contract_version: str
    candidate_ref_contract_version: str
    graph_schema_version: str
    required_routes: frozenset[EngineRoute] = Field(default_factory=frozenset)
    vector_required: bool = False


class EngineCapabilityProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    projection_event_versions: frozenset[str]
    projection_dto_versions: frozenset[str]
    candidate_ref_versions: frozenset[str]
    graph_schema_versions: frozenset[str]
    routes: frozenset[EngineRoute]
    vector_enabled: bool


class CapabilityEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    compatible: bool
    code: CapabilityFailureCode | None = None
    reason: str | None = None


def load_contract_fixture(path: Path) -> dict[str, Any]:
    """Load only synthetic capability fixtures used by Service contract tests."""
    with path.open(encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)

    if not isinstance(fixture, dict):
        raise ValueError("contract fixture must contain a JSON object")
    return fixture


def evaluate_capability(
    requirement: ServiceCapabilityRequirement, capability: EngineCapabilityProfile
) -> CapabilityEvaluation:
    checks = (
        (
            "projection event contract",
            requirement.projection_event_contract_version,
            capability.projection_event_versions,
        ),
        (
            "projection DTO contract",
            requirement.projection_dto_contract_version,
            capability.projection_dto_versions,
        ),
        (
            "candidate reference contract",
            requirement.candidate_ref_contract_version,
            capability.candidate_ref_versions,
        ),
        ("graph schema", requirement.graph_schema_version, capability.graph_schema_versions),
    )
    for capability_name, required_version, supported_versions in checks:
        if required_version not in supported_versions:
            return _incompatible(f"unsupported {capability_name}")

    missing_routes = requirement.required_routes.difference(capability.routes)
    if missing_routes:
        missing_route_names = ", ".join(sorted(route.value for route in missing_routes))
        return _incompatible(f"unsupported route capability: {missing_route_names}")

    if requirement.vector_required and not capability.vector_enabled:
        return _incompatible("unsupported vector capability")

    return CapabilityEvaluation(compatible=True)


def _incompatible(reason: str) -> CapabilityEvaluation:
    return CapabilityEvaluation(
        compatible=False,
        code=CapabilityFailureCode.CONTRACT_INCOMPATIBLE,
        reason=reason,
    )
