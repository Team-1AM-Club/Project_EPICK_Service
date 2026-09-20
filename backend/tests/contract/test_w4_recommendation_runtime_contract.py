from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from jsonschema import Draft202012Validator
from sqlalchemy import String

from app.models.recommendations import RecommendationCandidate
from app.services.recommendations import (
    W4_SYNTHETIC_QUESTION_SCOPE_ID,
    RecommendationService,
)

BACKEND_ROOT = Path(__file__).parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
CONTRACT_ROOT = BACKEND_ROOT / "contracts" / "w4" / "v1"
W4_ROOT = REPOSITORY_ROOT / "w4" / "EPICK_W4_HTTP_Runtime_2026-09-20_r5"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_recommendation_contract_manifest_pins_delivered_w4_source_and_schemas() -> None:
    manifest = json.loads(
        (CONTRACT_ROOT / "recommendation-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["w4_source_revision"] == "df41433218918e4167784243dc9b88e5a858278d"
    assert manifest["real_data_enabled"] is False

    for relative, expected in manifest["w4_files"].items():
        assert _sha256(W4_ROOT / relative) == expected
    for relative, expected in manifest["schemas"].items():
        schema_path = CONTRACT_ROOT / relative
        assert _sha256(schema_path) == expected
        Draft202012Validator.check_schema(json.loads(schema_path.read_text(encoding="utf-8")))


def test_private_w4_contract_is_not_exposed_by_public_openapi() -> None:
    public_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            BACKEND_ROOT / "app" / "main.py",
            BACKEND_ROOT / "app" / "api" / "v1" / "router.py",
        )
    )
    assert "app.api.private" not in public_sources
    assert "/internal/v1/w4/recommendations" not in public_sources
    assert "W4_RECOMMENDATION_PRIVATE_BEARER" not in public_sources


def test_w1_synthetic_engine_context_matches_delivered_w4_c01_knowledge_schema() -> None:
    owner_id = uuid4()
    source_id = uuid4()
    source_version_id = uuid4()
    context = RecommendationService._engine_context(
        run=SimpleNamespace(id=uuid4(), project_id=uuid4(), snapshot_id=uuid4()),
        owner_user_id=owner_id,
        question_scope_id="t104-synthetic-question-scope",
        context_version="w1-t104-synthetic-v1",
        episode_versions=(
            SimpleNamespace(
                episode_id=uuid4(),
                version_no=1,
                activity_id=uuid4(),
                title="T104 synthetic episode",
                original_narrative="Synthetic acceptance narrative.",
                situation_text=None,
                problem_text=None,
                goal_text=None,
                actions_text=None,
                result_text=None,
                learning_text=None,
            ),
        ),
        source=SimpleNamespace(id=source_id, source_type="CAREERS"),
        source_version=SimpleNamespace(
            id=source_version_id,
            version_no=1,
            content_hash="a" * 64,
            content_normalization_version="t104-v1",
        ),
    )
    knowledge = context["company_knowledge"]["sources"][0]["knowledge"]
    schema = json.loads(
        (W4_ROOT / "epick_w4" / "c01_schemas" / "knowledge.schema.json").read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator(schema).validate(knowledge)
    assert knowledge["bundle"]["evidences"]
    assert knowledge["bundle"]["limitations"][0]["code"] == (
        "W4_SYNTHETIC_ACCEPTANCE_ONLY"
    )


def test_w1_acceptance_scope_matches_delivered_w4_criteria_catalog() -> None:
    catalog = json.loads(
        (W4_ROOT / "epick_w4" / "criteria_catalog.json").read_text(encoding="utf-8")
    )

    assert W4_SYNTHETIC_QUESTION_SCOPE_ID == catalog["scope_id"]
    assert any(item["question_id"] == "job_experience" for item in catalog["questions"])


def test_w1_candidate_result_version_can_store_delivered_w4_digest_format() -> None:
    result_version_column = RecommendationCandidate.__table__.c.result_version

    assert isinstance(result_version_column.type, String)
    assert result_version_column.type.length == 128
    assert len("w4-" + ("a" * 64)) <= result_version_column.type.length
