"""Joint CT15 count inspection over two isolated owners sharing one synthetic Source."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import select
from tests.integration.source_collection.test_private_commit_gate import (
    NOW,
    _gate,
    _pair,
    apply_commit_gate,
    database_engine,
    session_factory,
    stage_private_result,
)

from epick_engine.source_collection.commit_gate_store import PrivateStagedOutbox
from epick_engine.source_collection.ct15_inspection import (
    Ct15InspectionError,
    inspect_run_counts,
    parse_run_scope,
)

__all__ = ["database_engine", "session_factory"]
pytestmark = pytest.mark.approved_postgres


def _scope(primary, secondary, *, source_id=None):
    return parse_run_scope(
        {
            "schema_version": "w2.ct15.run-inspection.scope.v1",
            "run_id": "ct15-synthetic-two-owner",
            "source_id": str(source_id or primary.source_id),
            "primary": {
                "owner_ref": str(primary.authenticated_owner_ref),
                "job_id": str(primary.job_id),
                "command_id": str(primary.command_id),
            },
            "secondary": {
                "owner_ref": str(secondary.authenticated_owner_ref),
                "job_id": str(secondary.job_id),
                "command_id": str(secondary.command_id),
            },
        }
    )


def _apply(session, command, result, action, *, operation_id, revision):
    return apply_commit_gate(
        session,
        _gate(command, result, action, operation_id=operation_id, revision=revision),
        ack_message_id=uuid4(),
        occurred_at=NOW,
    )


def test_joint_counts_sum_two_scopes_and_primary_purge_does_not_change_secondary(
    session_factory,
) -> None:
    primary, primary_result = _pair()
    secondary, secondary_result = _pair()
    assert primary.source_id == secondary.source_id
    primary_operation, secondary_operation = uuid4(), uuid4()

    with session_factory.begin() as session:
        stage_private_result(session, primary, primary_result, message_id=uuid4(), occurred_at=NOW)
        stage_private_result(
            session, secondary, secondary_result, message_id=uuid4(), occurred_at=NOW
        )
        _apply(
            session,
            primary,
            primary_result,
            "PREPARE",
            operation_id=primary_operation,
            revision=1,
        )
        _apply(
            session,
            primary,
            primary_result,
            "PURGE",
            operation_id=primary_operation,
            revision=2,
        )
        _apply(
            session,
            secondary,
            secondary_result,
            "PREPARE",
            operation_id=secondary_operation,
            revision=1,
        )
        _apply(
            session,
            secondary,
            secondary_result,
            "FINALIZE",
            operation_id=secondary_operation,
            revision=2,
        )
        inspection = inspect_run_counts(session, _scope(primary, secondary))

    counts = inspection["counts"]
    assert counts["primary"]["state_counts"]["PURGED"] == 1
    assert counts["secondary"]["state_counts"]["FINALIZED"] == 1
    assert counts["primary"]["visible_result_count"] == 0
    assert counts["secondary"]["visible_result_count"] == 1
    assert counts["total"]["state_counts"] == {
        "STAGED": 0,
        "PREPARED": 0,
        "FINALIZED": 1,
        "ABORTED": 0,
        "PURGED": 1,
    }
    assert counts["total"]["ack_count"] == 4
    assert inspection["source_verification"] == {
        "primary": "erased_unverifiable",
        "secondary": "matched",
    }

    schema_path = (
        Path(__file__).resolve().parents[3] / "contracts/w2-private/ct15-run-inspection.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(inspection)
    serialized = json.dumps(inspection, sort_keys=True)
    for private_value in (
        primary.authenticated_owner_ref,
        primary.job_id,
        primary.command_id,
        primary.source_id,
        secondary.authenticated_owner_ref,
        secondary.job_id,
        secondary.command_id,
        primary_result.message_ko,
        secondary_result.message_ko,
    ):
        assert str(private_value) not in serialized

    def nested_keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from nested_keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from nested_keys(item)

    forbidden_keys = {
        "body",
        "payload",
        "hash",
        "dsn",
        "owner_ref",
        "job_id",
        "command_id",
        "source_id",
        "result_digest",
    }
    assert forbidden_keys.isdisjoint(nested_keys(inspection))


def test_joint_inspection_rejects_stored_source_mismatch_with_fixed_error(session_factory) -> None:
    primary, primary_result = _pair()
    secondary, secondary_result = _pair()
    with session_factory.begin() as session:
        stage_private_result(session, primary, primary_result, message_id=uuid4(), occurred_at=NOW)
        stage_private_result(
            session, secondary, secondary_result, message_id=uuid4(), occurred_at=NOW
        )
        with pytest.raises(
            Ct15InspectionError, match="^CT15 run inspection scope mismatch$"
        ) as caught:
            inspect_run_counts(session, _scope(primary, secondary, source_id=uuid4()))
    assert str(primary.source_id) not in str(caught.value)


def test_joint_inspection_allows_two_explicit_missing_scopes_without_global_totals(
    session_factory,
) -> None:
    primary, _ = _pair()
    secondary, _ = _pair()
    with session_factory.begin() as session:
        inspection = inspect_run_counts(session, _scope(primary, secondary))
    assert set(inspection["counts"]) == {"primary", "secondary", "total"}
    assert inspection["source_verification"] == {"primary": "absent", "secondary": "absent"}
    assert sum(inspection["counts"]["total"]["state_counts"].values()) == 0


def test_reused_session_refreshes_staged_payload_after_other_session_purge(
    session_factory,
) -> None:
    primary, primary_result = _pair()
    secondary, secondary_result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        stage_private_result(session, primary, primary_result, message_id=uuid4(), occurred_at=NOW)
        stage_private_result(
            session, secondary, secondary_result, message_id=uuid4(), occurred_at=NOW
        )

    reused = session_factory()
    try:
        with reused.begin():
            cached = reused.scalar(
                select(PrivateStagedOutbox).where(
                    PrivateStagedOutbox.command_id == primary.command_id
                )
            )
            assert cached is not None and cached.payload is not None

        with session_factory.begin() as session:
            _apply(
                session,
                primary,
                primary_result,
                "PREPARE",
                operation_id=operation,
                revision=1,
            )
            _apply(
                session,
                primary,
                primary_result,
                "PURGE",
                operation_id=operation,
                revision=2,
            )

        with reused.begin():
            inspection = inspect_run_counts(reused, _scope(primary, secondary))
        assert cached.payload is None
        assert inspection["source_verification"]["primary"] == "erased_unverifiable"
        assert inspection["counts"]["primary"]["staged_payload_count"] == 0
        assert inspection["counts"]["secondary"]["staged_payload_count"] == 1
    finally:
        reused.close()
