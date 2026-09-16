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
def test_synthetic_run_acceptance_is_separate_from_executor_and_owner_scoped(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = recommendation_api_client
    override_principal(app, ids["one"])
    workspace = create_recommendation_workspace(
        client,
        company_id=ids["company"],
        suffix="acceptance",
    )

    accepted = create_synthetic_run(
        client,
        question_id=workspace["question_id"],
        key="recommendation-run-acceptance",
        candidate_limit=1,
    )
    replayed = create_synthetic_run(
        client,
        question_id=workspace["question_id"],
        key="recommendation-run-acceptance",
        candidate_limit=1,
    )
    run_id = accepted.json()["id"]

    assert accepted.status_code == 202
    assert replayed.status_code == 202
    assert replayed.json() == accepted.json()
    assert accepted.headers["Location"] == f"/api/v1/recommendation-runs/{run_id}"
    assert accepted.json()["status"] == "PENDING"
    assert accepted.json()["result_status"] == "PENDING"
    assert accepted.json()["result_origin"] == "SYNTHETIC"
    assert client.get(accepted.json()["candidates_url"]).json() == {
        "items": [],
        "next_cursor": None,
    }

    execute_synthetic_run(factory, owner_user_id=ids["one"], run_id=run_id)
    completed = client.get(f"/api/v1/recommendation-runs/{run_id}")

    assert completed.status_code == 200
    assert completed.json()["status"] == "SUCCEEDED"
    assert completed.json()["result_status"] == "READY"
    assert completed.json()["result_origin"] == "SYNTHETIC"

    stale_snapshot = create_synthetic_run(
        client,
        question_id=workspace["question_id"],
        key="recommendation-run-stale-snapshot",
        candidate_limit=1,
    )
    assert stale_snapshot.status_code == 409
    assert stale_snapshot.json()["error"]["code"] == "STALE_INPUT"

    override_principal(app, ids["two"])
    hidden = client.get(f"/api/v1/recommendation-runs/{run_id}")
    assert hidden.status_code == 404


@pytest.mark.postgres
def test_candidate_list_uses_signed_cursor_after_separate_synthetic_execution(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = recommendation_api_client
    override_principal(app, ids["one"])
    workspace = create_recommendation_workspace(
        client,
        company_id=ids["company"],
        suffix="pagination",
        episode_count=2,
    )
    accepted = create_synthetic_run(
        client,
        question_id=workspace["question_id"],
        key="recommendation-run-pagination",
        candidate_limit=2,
    )
    run_id = accepted.json()["id"]
    execute_synthetic_run(factory, owner_user_id=ids["one"], run_id=run_id)

    first_page = client.get(f"/api/v1/recommendation-runs/{run_id}/candidates", params={"limit": 1})
    second_page = client.get(
        f"/api/v1/recommendation-runs/{run_id}/candidates",
        params={"limit": 1, "cursor": first_page.json()["next_cursor"]},
    )

    assert first_page.status_code == 200
    assert len(first_page.json()["items"]) == 1
    assert first_page.json()["next_cursor"] is not None
    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 1
    assert second_page.json()["next_cursor"] is None
