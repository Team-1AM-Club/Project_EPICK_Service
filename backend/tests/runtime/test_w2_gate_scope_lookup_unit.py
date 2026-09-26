from __future__ import annotations

from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.runtime import lookup_adapter
from app.runtime import w2_private_write_authority as authority


def _request() -> authority.GateScopeLookupRequest:
    return authority.GateScopeLookupRequest(
        schema_version="w1.private.w2-gate-scope-lookup.v1",
        owner_user_id=uuid4(),
        owner_deletion_epoch=0,
        command_id=uuid4(),
        job_id=uuid4(),
        execution_fence=1,
        operation_id=uuid4(),
        operation_revision=2,
        action="ABORT",
        phase="APPLY",
        result_digest="sha256:" + "a" * 64,
    )


@pytest.mark.parametrize("project_scoped", [False, True])
def test_gate_scope_lookup_uses_exact_issued_gate_then_existing_authority(
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
                }
            }
        ),
        Mock(**{"mappings.return_value.one_or_none.return_value": {"project_id": project_id}}),
    )
    session.scalar.return_value = True
    set_owner = Mock()
    decide = Mock()
    monkeypatch.setattr(authority, "set_local_owner_context", set_owner)
    monkeypatch.setattr(authority, "decide_gate_authority", decide)

    response = authority.resolve_gate_scope(session=session, request=request)

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
    assert checked.schema_version == "w1.private.w2-gate-authority.v1"
    assert checked.operation_id == request.operation_id
    assert checked.operation_revision == request.operation_revision
    assert checked.action == request.action


def test_gate_scope_lookup_rejects_missing_issuance_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    session = Mock()
    session.execute.side_effect = (
        Mock(
            **{
                "mappings.return_value.one_or_none.return_value": {
                    "owner_user_id": request.owner_user_id,
                    "job_id": request.job_id,
                }
            }
        ),
        Mock(**{"mappings.return_value.one_or_none.return_value": {"project_id": None}}),
    )
    session.scalar.return_value = False
    monkeypatch.setattr(authority, "set_local_owner_context", Mock())
    decide = Mock()
    monkeypatch.setattr(authority, "decide_gate_authority", decide)

    with pytest.raises(authority.PrivateWriteAuthorityDenied, match="not issued"):
        authority.resolve_gate_scope(session=session, request=request)
    decide.assert_not_called()


def test_gate_scope_lookup_rejects_other_owner_before_setting_rls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    session = Mock()
    session.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "owner_user_id": uuid4(),
        "job_id": request.job_id,
    }
    set_owner = Mock()
    monkeypatch.setattr(authority, "set_local_owner_context", set_owner)

    with pytest.raises(authority.PrivateWriteAuthorityDenied, match="command binding"):
        authority.resolve_gate_scope(session=session, request=request)
    set_owner.assert_not_called()
    session.scalar.assert_not_called()


def test_gate_scope_lookup_route_uses_w2_auth_and_opaque_database_failure() -> None:
    session_factory = Mock()
    session_factory.begin.side_effect = SQLAlchemyError("sensitive database failure")
    client = TestClient(
        lookup_adapter.create_lookup_app(
            session_factory=session_factory, expected_bearer_token="test-w2-bearer"
        )
    )
    request = _request().model_dump(mode="json", exclude_none=True)
    url = "/internal/v1/w2-private/gate-scope-lookup"
    assert client.post(url, json=request).status_code == 401
    assert (
        client.post(
            url,
            json=request,
            headers={
                "Authorization": "Bearer test-w2-bearer",
                "X-EPICK-Service-Principal": "w3",
            },
        ).status_code
        == 403
    )
    response = client.post(
        url,
        json=request,
        headers={
            "Authorization": "Bearer test-w2-bearer",
            "X-EPICK-Service-Principal": "w2",
        },
    )
    assert response.status_code == 503
    assert response.json()["code"] == "INTERNAL_RETRYABLE"
    assert "sensitive" not in response.text
