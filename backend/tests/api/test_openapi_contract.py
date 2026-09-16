from __future__ import annotations

from typing import Any

from app.main import create_app


def _response_schema(operation: dict[str, Any], status_code: str) -> dict[str, Any]:
    return operation["responses"][status_code]["content"]["application/json"]["schema"]


def test_openapi_documents_the_public_v1_surface_and_status_contracts() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]

    assert schema["info"] == {
        "title": "EPICK Service API",
        "description": schema["info"]["description"],
        "version": "1.0.0",
    }
    assert {tag["name"] for tag in schema["tags"]} >= {
        "activities",
        "application-projects",
        "jobs",
        "recommendations",
        "account-deletion",
    }

    assert {"get", "post"} <= set(paths["/api/v1/activities"])
    assert "202" in paths["/api/v1/questions/{question_id}/recommendation-runs"]["post"][
        "responses"
    ]
    assert "202" in paths["/api/v1/jobs/{job_id}/retry"]["post"]["responses"]
    assert "202" in paths["/api/v1/account/deletion-requests"]["post"]["responses"]
    assert {"200", "503"} <= set(paths["/health/ready"]["get"]["responses"])


def test_openapi_uses_public_request_response_and_error_envelopes() -> None:
    schema = create_app().openapi()
    components = schema["components"]["schemas"]
    paths = schema["paths"]
    retry = paths["/api/v1/jobs/{job_id}/retry"]["post"]
    activity_list = paths["/api/v1/activities"]["get"]

    assert retry["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/JobRetryRequest"
    }
    assert _response_schema(retry, "202") == {"$ref": "#/components/schemas/JobResponse"}
    assert _response_schema(retry, "401") == {"$ref": "#/components/schemas/ApiErrorResponse"}
    assert _response_schema(retry, "409") == {"$ref": "#/components/schemas/ApiErrorResponse"}
    assert _response_schema(retry, "422") == {"$ref": "#/components/schemas/ApiErrorResponse"}

    list_schema_ref = _response_schema(activity_list, "200")["$ref"].rsplit("/", maxsplit=1)[-1]
    assert set(components[list_schema_ref]["properties"]) == {"items", "next_cursor"}
    assert set(components["ApiErrorResponse"]["properties"]) == {"error"}
    assert set(components["ApiErrorBody"]["properties"]) == {
        "code",
        "message_ko",
        "retryable",
        "actions",
        "correlation_id",
        "fields",
    }


def test_openapi_preserves_public_job_enums() -> None:
    components = create_app().openapi()["components"]["schemas"]
    job_properties = components["JobResponse"]["properties"]

    assert set(job_properties["status"]["enum"]) == {
        "QUEUED",
        "RUNNING",
        "WAITING_USER",
        "PAUSED_RATE_LIMIT",
        "SUCCEEDED",
        "FAILED_RETRYABLE",
        "FAILED_FINAL",
        "CANCEL_REQUESTED",
        "CANCELLED",
    }
    assert set(job_properties["dispatch_status"]["enum"]) == {
        "OUTBOX_PENDING",
        "ENQUEUED",
        "CLAIMED",
        "BLOCKED",
        "INVALIDATED",
    }
