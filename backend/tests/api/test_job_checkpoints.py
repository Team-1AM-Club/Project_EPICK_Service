from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tests.api.conftest import override_principal
from tests.api.job_support import pause_job_with_action


@pytest.mark.postgres
def test_checkpoint_exposes_only_current_safe_summary(
    job_api_client: tuple[TestClient, FastAPI, dict[str, UUID], sessionmaker],
) -> None:
    client, app, ids, factory = job_api_client
    paused = pause_job_with_action(
        factory,
        owner_user_id=ids["one"],
        key="job-checkpoint",
        status="PAUSED_RATE_LIMIT",
        action_code="RETRY",
        checkpoint=True,
    )
    override_principal(app, ids["one"])

    checkpoint = client.get(f"/api/v1/jobs/{paused['job_id']}/checkpoint")

    assert checkpoint.status_code == 200
    assert checkpoint.json() == {
        "available": True,
        "last_completed_stage": "RETRIEVING_CANDIDATES",
        "analysis_input_version": "analysis-v1",
    }
    assert "resume_payload" not in checkpoint.json()
    assert "state_ref" not in checkpoint.json()
