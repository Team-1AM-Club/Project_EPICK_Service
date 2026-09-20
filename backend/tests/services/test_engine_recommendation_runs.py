from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.recommendations import RecommendationRun
from app.services.recommendations import RecommendationService


def _pending_run(origin: str = "SYNTHETIC") -> RecommendationRun:
    return RecommendationRun(
        id=uuid4(),
        owner_user_id=uuid4(),
        project_id=uuid4(),
        question_id=uuid4(),
        question_version_id=uuid4(),
        snapshot_id=uuid4(),
        analysis_policy_version="test-v1",
        result_origin=origin,
        requested_candidate_limit=3,
        limitations=[],
    )


def _arguments(run: RecommendationRun) -> dict[str, object]:
    return {
        "owner_user_id": run.owner_user_id,
        "question_id": run.question_id,
        "expected_question_version": 1,
        "expected_snapshot_no": 1,
        "requested_candidate_limit": 3,
        "include_excluded": False,
        "allow_limited_analysis": True,
    }


def test_server_selected_synthetic_mode_preserves_existing_executor(monkeypatch) -> None:
    run = _pending_run()
    service = RecommendationService(Mock())
    service.create_synthetic_recommendation_run = Mock(return_value=run)
    service._adopt_engine_execution = Mock()
    monkeypatch.setattr(settings, "w1_recommendation_execution_mode", "SYNTHETIC")

    assert service.create_server_selected_recommendation_run(**_arguments(run)) is run
    service._adopt_engine_execution.assert_not_called()
    assert run.result_origin == "SYNTHETIC"


def test_server_selected_engine_mode_is_durable_acceptance_not_inline_execution(
    monkeypatch,
) -> None:
    run = _pending_run()
    service = RecommendationService(Mock())
    service.create_synthetic_recommendation_run = Mock(return_value=run)
    service._adopt_engine_execution = Mock()
    monkeypatch.setattr(settings, "w1_recommendation_execution_mode", "ENGINE")

    assert service.create_server_selected_recommendation_run(**_arguments(run)) is run
    service._adopt_engine_execution.assert_called_once_with(
        owner_user_id=run.owner_user_id,
        run=run,
    )


def test_engine_acceptance_failure_does_not_fall_back_to_synthetic(monkeypatch) -> None:
    run = _pending_run()
    service = RecommendationService(Mock())
    service.create_synthetic_recommendation_run = Mock(return_value=run)
    service._adopt_engine_execution = Mock(side_effect=RuntimeError("durable acceptance failed"))
    monkeypatch.setattr(settings, "w1_recommendation_execution_mode", "ENGINE")

    with pytest.raises(RuntimeError, match="durable acceptance failed"):
        service.create_server_selected_recommendation_run(**_arguments(run))
    assert run.result_origin == "SYNTHETIC"
