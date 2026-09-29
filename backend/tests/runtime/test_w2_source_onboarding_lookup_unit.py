from __future__ import annotations

from unittest.mock import MagicMock, Mock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.runtime import lookup_adapter

URL = "/internal/v1/w2-private/source-onboarding-lookup"
HEADERS = {
    "Authorization": "Bearer test-w2-bearer",
    "X-EPICK-Service-Principal": "w2",
}


def _request() -> lookup_adapter.LookupRequest:
    return lookup_adapter.LookupRequest(
        schema_version="w1.private.command-lookup.v1",
        command_id=uuid4(),
        execution_fence=1,
        owner_deletion_epoch=0,
    )


def test_onboarding_lookup_requires_w2_identity_and_valid_command(monkeypatch) -> None:
    request = _request()
    source_id, company_id = uuid4(), uuid4()
    canonical = lookup_adapter.LookupResponse(
        command_id=request.command_id,
        status="AVAILABLE",
        reason_code=None,
        command={"source_id": str(source_id), "company_id": str(company_id)},
    )
    checked = Mock(return_value=canonical)
    monkeypatch.setattr(lookup_adapter, "_lookup_command", checked)
    source = {
        "id": source_id,
        "company_id": company_id,
        "canonical_url": "https://careers.example.test/job/1",
        "source_type": "CAREERS",
        "title": None,
    }
    company = {
        "id": company_id,
        "legal_name": "Example",
        "official_domain": "example.test",
        "identification_status": "VERIFIED",
    }
    session = Mock()
    session.execute.side_effect = [
        Mock(**{"mappings.return_value.one_or_none.return_value": source}),
        Mock(**{"mappings.return_value.one_or_none.return_value": company}),
    ]
    factory = MagicMock()
    factory.begin.return_value.__enter__.return_value = session
    client = TestClient(
        lookup_adapter.create_lookup_app(
            session_factory=factory, expected_bearer_token="test-w2-bearer"
        )
    )

    body = request.model_dump(mode="json")
    assert client.post(URL, json=body).status_code == 401
    assert (
        client.post(
            URL,
            json=body,
            headers={**HEADERS, "X-EPICK-Service-Principal": "w3"},
        ).status_code
        == 403
    )
    response = client.post(URL, json=body, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "AVAILABLE"
    assert response.json()["source"]["canonical_url"] == source["canonical_url"]
    assert "collection_permission" not in response.json()["source"]
    checked.assert_called_once()
    assert session.execute.call_count == 2


def test_onboarding_lookup_does_not_read_source_after_stale_command(monkeypatch) -> None:
    request = _request()
    monkeypatch.setattr(
        lookup_adapter,
        "_lookup_command",
        Mock(
            return_value=lookup_adapter.LookupResponse(
                command_id=request.command_id,
                status="STALE_FENCE",
                reason_code="EXECUTION_FENCE_MISMATCH",
                command=None,
            )
        ),
    )
    session = Mock()
    response = lookup_adapter._lookup_source_onboarding(session=session, request=request)
    assert response.status == "UNAVAILABLE"
    assert response.source is None
    session.execute.assert_not_called()
