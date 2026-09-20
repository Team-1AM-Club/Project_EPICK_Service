from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID

JOB_ID = UUID("00000000-0000-4000-8000-000000000301")
ACTION_ID = UUID("00000000-0000-4000-8000-000000000302")


def public_job_payload(**overrides: Any) -> dict[str, Any]:
    """Return a complete synthetic public Job payload with no private transport fields."""

    payload: dict[str, Any] = {
        "id": str(JOB_ID),
        "job_type": "QUESTION_ANALYSIS",
        "status": "WAITING_USER",
        "completeness": "partial",
        "dispatch_status": "BLOCKED",
        "stage": "COLLECTING_SOURCES",
        "input_refs": [],
        "progress": {"completed_units": 1, "total_units": 4, "percent": 25},
        "required_actions": [
            {
                "id": str(ACTION_ID),
                "code": "CONTINUE_LIMITED",
                "status": "OPEN",
                "context_code": "MISSING_OPTIONAL_SOURCE",
                "expected_input_version": "input-v1",
                "expected_result_version": "result-v1",
            }
        ],
        "checkpoint": {
            "available": True,
            "last_completed_stage": "COLLECTING_SOURCES",
            "analysis_input_version": "input-v1",
        },
        "failure": {
            "code": None,
            "message": None,
            "retryable": False,
            "retry_after_seconds": None,
        },
        "limitations": ["공식 공고 일부 누락"],
        "created_at": "2026-09-20T00:00:00Z",
        "updated_at": "2026-09-20T00:00:03Z",
    }
    payload.update(deepcopy(overrides))
    return payload
