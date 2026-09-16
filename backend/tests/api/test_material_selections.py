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
def test_candidate_selection_rechecks_result_version_replaces_and_can_be_cleared(
    recommendation_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = recommendation_api_client
    override_principal(app, ids["one"])
    workspace = create_recommendation_workspace(
        client,
        company_id=ids["company"],
        suffix="selection",
        episode_count=2,
    )
    accepted = create_synthetic_run(
        client,
        question_id=workspace["question_id"],
        key="recommendation-run-selection",
        candidate_limit=2,
    )
    run_id = accepted.json()["id"]
    execute_synthetic_run(factory, owner_user_id=ids["one"], run_id=run_id)
    candidates = client.get(f"/api/v1/recommendation-runs/{run_id}/candidates").json()["items"]
    first_candidate, second_candidate = candidates
    selection_body = {
        "question_id": workspace["question_id"],
        "recommendation_run_id": run_id,
        "result_version": first_candidate["result_version"],
    }

    created = client.post(
        f"/api/v1/recommendation-candidates/{first_candidate['id']}/select",
        json=selection_body,
        headers={"Idempotency-Key": "selection-create"},
    )
    current = client.get(f"/api/v1/questions/{workspace['question_id']}/selection")
    stale = client.post(
        f"/api/v1/recommendation-candidates/{first_candidate['id']}/select",
        json={**selection_body, "result_version": "synthetic-v0"},
        headers={"Idempotency-Key": "selection-stale-result"},
    )
    same_candidate = client.post(
        f"/api/v1/recommendation-candidates/{first_candidate['id']}/select",
        json=selection_body,
        headers={"Idempotency-Key": "selection-same-candidate"},
    )
    conflict = client.post(
        f"/api/v1/recommendation-candidates/{second_candidate['id']}/select",
        json={**selection_body, "result_version": second_candidate["result_version"]},
        headers={"Idempotency-Key": "selection-conflict"},
    )
    replaced = client.post(
        f"/api/v1/recommendation-candidates/{second_candidate['id']}/select",
        json={
            **selection_body,
            "result_version": second_candidate["result_version"],
            "replace_existing": True,
        },
        headers={"Idempotency-Key": "selection-replace"},
    )

    assert created.status_code == 201
    assert created.json()["project_id"] == workspace["project_id"]
    assert current.json() == created.json()
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "STALE_INPUT"
    assert same_candidate.status_code == 200
    assert same_candidate.json()["selection_id"] == created.json()["selection_id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "ACTION_NOT_ALLOWED"
    assert replaced.status_code == 201
    assert replaced.json()["candidate_id"] == second_candidate["id"]

    deleted = client.delete(
        f"/api/v1/questions/{workspace['question_id']}/selection",
        headers={"Idempotency-Key": "selection-delete"},
    )
    missing = client.get(f"/api/v1/questions/{workspace['question_id']}/selection")
    assert deleted.status_code == 204
    assert missing.status_code == 404
