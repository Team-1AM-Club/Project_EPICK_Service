from __future__ import annotations

import pytest
from sqlalchemy import Engine, inspect


@pytest.mark.postgres
def test_api_contract_migration_adds_only_the_declared_postgresql_columns(
    api_migrated_engine: Engine,
) -> None:
    inspector = inspect(api_migrated_engine)
    recommendation_columns = {
        column["name"] for column in inspector.get_columns("recommendation_runs")
    }
    action_columns = {column["name"] for column in inspector.get_columns("job_required_actions")}
    idempotency_columns = {
        column["name"] for column in inspector.get_columns("idempotency_records")
    }
    recommendation_checks = {
        constraint["name"] for constraint in inspector.get_check_constraints("recommendation_runs")
    }

    assert "result_origin" in recommendation_columns
    assert {"expected_input_version", "expected_result_version"} <= action_columns
    assert "response_body" in idempotency_columns
    assert "ck_recommendation_runs_result_origin_allowed" in recommendation_checks
