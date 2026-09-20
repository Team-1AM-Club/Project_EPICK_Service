from __future__ import annotations

import json

from scripts.export_openapi import render_openapi


def test_openapi_export_is_reproducible_and_sorted() -> None:
    first = render_openapi()
    second = render_openapi()

    assert first == second
    assert first.endswith("\n")
    assert json.loads(first)["openapi"].startswith("3.")
    assert first.index('"components"') < first.index('"info"') < first.index('"openapi"')


def test_public_error_envelope_remains_closed_and_stable() -> None:
    document = json.loads(render_openapi())
    schema = document["components"]["schemas"]["ApiErrorResponse"]
    body_ref = schema["properties"]["error"]["$ref"]
    body = document["components"]["schemas"][body_ref.rsplit("/", 1)[-1]]

    assert schema["additionalProperties"] is False
    assert body["additionalProperties"] is False
    assert set(body["required"]) == {"code", "correlation_id", "message_ko"}
    assert set(body["properties"]) == {
        "actions",
        "code",
        "correlation_id",
        "fields",
        "message_ko",
        "retryable",
    }
