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


def test_recommendation_openapi_preserves_version_origin_and_selection_contract(
    api_app: FastAPI,
) -> None:
    schemas = api_app.openapi()["components"]["schemas"]

    run = schemas["RecommendationRunResponse"]
    assert run["properties"]["status"]["enum"] == [
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "LIMITED",
        "FAILED",
        "CANCELLED",
    ]
    assert run["properties"]["result_status"]["enum"] == [
        "PENDING",
        "READY",
        "LIMITED",
        "FAILED",
    ]
    assert run["properties"]["result_origin"]["enum"] == ["SYNTHETIC", "ENGINE"]
    assert {"question_version", "snapshot_version", "result_origin"}.issubset(run["required"])

    candidate = schemas["RecommendationCandidateResponse"]
    assert {
        "id",
        "match_status",
        "short_reason",
        "strength_summary",
        "limitation_summary",
        "validation_status",
        "result_version",
    }.issubset(candidate["required"])
    assert not {
        "owner_user_id",
        "snapshot_id",
        "internal_rank",
        "embedding_id",
        "graph_node_id",
    }.intersection(candidate["properties"])

    request = schemas["CandidateSelectionRequest"]
    assert {"question_id", "recommendation_run_id", "result_version"}.issubset(request["required"])
    assert request["properties"]["replace_existing"]["default"] is False


@pytest.mark.postgres
def test_recommendation_candidate_and_selection_are_owner_scoped_with_same_404(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = recommendation_api_client
    override_principal(app, ids["one"])
    workspace = create_recommendation_workspace(
        client,
        company_id=ids["company"],
        suffix="frontend-contract",
    )
    accepted = create_synthetic_run(
        client,
        question_id=workspace["question_id"],
        key="frontend-contract-run",
        candidate_limit=1,
    )
    run_id = accepted.json()["id"]
    execute_synthetic_run(factory, owner_user_id=ids["one"], run_id=run_id)
    candidate = client.get(f"/api/v1/recommendation-runs/{run_id}/candidates").json()["items"][0]
    selected = client.post(
        f"/api/v1/recommendation-candidates/{candidate['id']}/select",
        json={
            "question_id": workspace["question_id"],
            "recommendation_run_id": run_id,
            "result_version": candidate["result_version"],
            "replace_existing": False,
        },
        headers={"Idempotency-Key": "frontend-contract-selection"},
    )
    assert selected.status_code == 201

    override_principal(app, ids["two"])
    paths = (
        f"/api/v1/recommendation-runs/{run_id}",
        f"/api/v1/recommendation-runs/{run_id}/candidates",
        f"/api/v1/recommendation-candidates/{candidate['id']}",
        f"/api/v1/questions/{workspace['question_id']}/selection",
    )
    for path in paths:
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
