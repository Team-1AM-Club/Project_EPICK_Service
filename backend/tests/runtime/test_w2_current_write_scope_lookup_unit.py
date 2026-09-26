from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.runtime import lookup_adapter
from app.runtime import w2_private_write_authority as authority

URL = "/internal/v1/w2-private/current-write-scope-lookup"
HEADERS = {
    "Authorization": "Bearer test-w2-bearer",
    "X-EPICK-Service-Principal": "w2",
}


def _request() -> authority.CurrentWriteScopeLookupRequest:
    return authority.CurrentWriteScopeLookupRequest(
        schema_version="w1.private.w2-current-write-scope-lookup.v1",
        owner_user_id=uuid4(),
        owner_deletion_epoch=0,
        command_id=uuid4(),
        job_id=uuid4(),
        execution_fence=1,
    )


@pytest.mark.parametrize("project_scoped", [False, True])
def test_current_scope_uses_w1_command_payload_and_fresh_authority(
    monkeypatch: pytest.MonkeyPatch, project_scoped: bool
) -> None:
    request = _request()
    project_id = uuid4() if project_scoped else None
    session = Mock()
    session.execute.side_effect = (
        Mock(
            **{
                "mappings.return_value.one_or_none.return_value": {
                    "owner_user_id": request.owner_user_id,
                    "job_id": request.job_id,
                    "command_type": "W2_SOURCE_COLLECTION",
                    "status": "ENQUEUED",
                    "execution_fence": 1,
                    "owner_deletion_epoch": 0,
                    "payload": {
                        "w2_command": {
                            "schema_version": "w2.collection.v1",
                            "command_id": str(request.command_id),
                            "job_id": str(request.job_id),
                            "authenticated_owner_ref": str(request.owner_user_id),
                            "execution_fence": "1",
                            "owner_deletion_epoch": 0,
                            "project_ref": str(project_id) if project_id is not None else None,
                        }
                    },
                }
            }
        ),
        Mock(
            **{
                "mappings.return_value.one_or_none.return_value": {
                    "project_id": project_id,
                    "status": "RUNNING",
                    "active_lease_id": uuid4(),
                }
            }
        ),
    )
    set_owner = Mock()
    decide = Mock()
    monkeypatch.setattr(authority, "set_local_owner_context", set_owner)
    monkeypatch.setattr(authority, "decide_private_write_authority", decide)

    response = authority.resolve_current_write_scope(session=session, request=request)

    expected_scope = (
        {"type": "PROJECT", "project_id": str(project_id)}
        if project_id is not None
        else {"type": "ACCOUNT"}
    )
    assert response.scope == expected_scope
    assert "authority_ref" not in response.model_dump()
    set_owner.assert_called_once_with(session, request.owner_user_id)
    checked = decide.call_args.kwargs["request"]
    assert checked.scope == expected_scope
    assert checked.schema_version == "w1.private.w2-write-authority.v1"


def test_current_scope_rejects_other_owner_before_setting_rls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    session = Mock()
    session.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "owner_user_id": uuid4(),
        "job_id": request.job_id,
        "command_type": "W2_SOURCE_COLLECTION",
        "payload": {},
    }
    set_owner = Mock()
    monkeypatch.setattr(authority, "set_local_owner_context", set_owner)
    with pytest.raises(authority.PrivateWriteAuthorityDenied, match="command binding"):
        authority.resolve_current_write_scope(session=session, request=request)
    set_owner.assert_not_called()


def test_current_scope_route_auth_validation_and_database_failure() -> None:
    session_factory = Mock()
    session_factory.begin.side_effect = SQLAlchemyError("sensitive database failure")
    client = TestClient(
        lookup_adapter.create_lookup_app(
            session_factory=session_factory, expected_bearer_token="test-w2-bearer"
        )
    )
    request = _request().model_dump(mode="json")
    assert client.post(URL, json=request).status_code == 401
    assert (
        client.post(
            URL,
            json=request,
            headers={**HEADERS, "X-EPICK-Service-Principal": "w3"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            URL, json={**request, "scope": {"type": "ACCOUNT"}}, headers=HEADERS
        ).status_code
        == 422
    )
    response = client.post(URL, json=request, headers=HEADERS)
    assert response.status_code == 503
    assert response.json()["code"] == "INTERNAL_RETRYABLE"
    assert "sensitive" not in response.text
