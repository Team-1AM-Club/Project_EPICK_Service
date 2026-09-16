from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.api.conftest import override_principal
from tests.api.recommendation_support import (
    create_recommendation_workspace,
    create_synthetic_run,
    execute_synthetic_run,
)


@pytest.mark.postgres
def test_candidate_detail_exposes_only_persisted_summary_and_hides_other_owner(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = recommendation_api_client
    override_principal(app, ids["one"])
    workspace = create_recommendation_workspace(
        client,
        company_id=ids["company"],
        suffix="candidate-detail",
    )
    accepted = create_synthetic_run(
        client,
        question_id=workspace["question_id"],
        key="recommendation-run-candidate-detail",
        candidate_limit=1,
    )
    run_id = accepted.json()["id"]
    execute_synthetic_run(factory, owner_user_id=ids["one"], run_id=run_id)

    listed = client.get(f"/api/v1/recommendation-runs/{run_id}/candidates")
    candidate_id = listed.json()["items"][0]["id"]
    detail = client.get(f"/api/v1/recommendation-candidates/{candidate_id}")

    assert detail.status_code == 200
    assert set(detail.json()) == {
        "id",
        "candidate_no",
        "episode_version_id",
        "match_status",
        "short_reason",
        "strength_summary",
        "limitation_summary",
        "validation_status",
        "result_version",
    }
    assert detail.json()["result_version"] == "synthetic-v1"
    assert "internal_rank" not in detail.json()

    override_principal(app, ids["two"])
    hidden = client.get(f"/api/v1/recommendation-candidates/{candidate_id}")
    assert hidden.status_code == 404
