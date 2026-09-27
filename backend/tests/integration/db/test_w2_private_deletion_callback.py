from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models.deletion import DeletionTarget
from app.models.identity import User
from app.runtime.w2_deletion_callback import create_w2_deletion_callback_app
from app.services.deletion import DeletionOrchestrationService

RUNTIME_PRIVILEGES_SQL = Path(__file__).parents[3] / "infra" / "postgres" / "runtime_privileges.sql"


@pytest.fixture(autouse=True)
def clean_rows(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))


@pytest.fixture
def callback_client(migrated_engine: Engine) -> Iterator[TestClient]:
    callback_engine = create_engine(migrated_engine.url.render_as_string(hide_password=False))

    @event.listens_for(callback_engine, "checkout")
    def set_deleter_role(dbapi_connection, connection_record, connection_proxy) -> None:
        del connection_record, connection_proxy
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET ROLE epick_deleter")

    try:
        yield TestClient(
            create_w2_deletion_callback_app(
                session_factory=sessionmaker(bind=callback_engine, expire_on_commit=False),
                expected_bearer_token="separate-w2-deletion-token",
            )
        )
    finally:
        callback_engine.dispose()


def test_private_callback_readiness_is_database_bound(
    callback_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = callback_client.get("/internal/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    with monkeypatch.context() as patch:

        def fail_database(*args: object, **kwargs: object) -> None:
            raise SQLAlchemyError("injected database outage")

        patch.setattr(Session, "execute", fail_database)
        unavailable = callback_client.get("/internal/health/ready")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"code": "INTERNAL_RETRYABLE"}
    assert "injected database outage" not in unavailable.text


def test_authenticated_w2_v2_ack_is_bound_to_current_target(
    migrated_engine: Engine, callback_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(migrated_engine, expire_on_commit=False) as session:
        owner = User(display_name="Callback owner", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        service = DeletionOrchestrationService(session)
        preview = service.create_account_deletion_preview(
            owner_user_id=owner.id, preview_token="callback-preview"
        )
        request = service.confirm_and_start_account_deletion(
            owner_user_id=owner.id,
            deletion_request_id=preview.request.id,
            preview_token=preview.preview_token,
        )
        target = session.scalar(
            select(DeletionTarget).where(
                DeletionTarget.deletion_request_id == request.id,
                DeletionTarget.store_type == "W2_SOURCE_RUNTIME",
            )
        )
        assert target is not None
        service.mark_target_dispatched(
            owner_user_id=owner.id,
            deletion_request_id=request.id,
            deletion_target_id=target.id,
        )
        owner_id, target_id, request_id = owner.id, target.id, request.id
        session.commit()

    body = {
        "schema_version": "w2.private-deletion-ack.v2",
        "deletion_id": str(target_id),
        "owner_user_id": str(owner_id),
        "deletion_epoch": 1,
        "scope": {"type": "ACCOUNT"},
        "outcome": "APPLIED",
    }
    url = "/internal/v1/w2-private/deletion/ack"
    headers = {
        "Authorization": "Bearer separate-w2-deletion-token",
        "X-EPICK-Service-Principal": "w2",
    }
    assert callback_client.post(url, json=body).status_code == 401
    assert (
        callback_client.post(
            url, json=body, headers={**headers, "X-EPICK-Service-Principal": "other"}
        ).status_code
        == 403
    )
    for rejected_body, code in (
        ({**body, "schema_version": "w2.private-deletion-ack.v1"}, "W2_DELETION_V2_ACK_INVALID"),
        ({**body, "deletion_id": str(uuid4())}, "W2_DELETION_V2_TARGET_NOT_FOUND"),
        ({**body, "owner_user_id": str(target_id)}, "W2_DELETION_V2_BINDING_INVALID"),
        ({**body, "deletion_epoch": 2}, "W2_DELETION_V2_STALE_EPOCH"),
        (
            {**body, "scope": {"type": "PROJECT", "project_id": str(uuid4())}},
            "W2_DELETION_V2_BINDING_INVALID",
        ),
    ):
        rejected = callback_client.post(url, json=rejected_body, headers=headers)
        assert rejected.status_code == 409
        assert rejected.json() == {"code": code}
    with Session(migrated_engine) as session:
        assert session.get(DeletionTarget, target_id).status == "DISPATCHED"

    with migrated_engine.begin() as connection:
        connection.execute(
            text("UPDATE deletion_requests SET status='EXPIRED' WHERE id=:id"), {"id": request_id}
        )
    blocked = callback_client.post(url, json=body, headers=headers)
    assert blocked.status_code == 409
    assert blocked.json() == {"code": "W2_DELETION_V2_STATE_INVALID"}
    with migrated_engine.begin() as connection:
        connection.execute(
            text("UPDATE deletion_requests SET status='RUNNING' WHERE id=:id"), {"id": request_id}
        )

    with monkeypatch.context() as patch:

        def fail_database(*args: object, **kwargs: object) -> None:
            raise SQLAlchemyError("injected database outage")

        patch.setattr(
            DeletionOrchestrationService, "apply_w2_private_deletion_ack_v2", fail_database
        )
        retryable = callback_client.post(url, json=body, headers=headers)
    assert retryable.status_code == 503
    assert retryable.json() == {"code": "INTERNAL_RETRYABLE"}
    assert "injected database outage" not in retryable.text
    with Session(migrated_engine) as session:
        assert session.get(DeletionTarget, target_id).status == "DISPATCHED"

    applied = callback_client.post(url, json=body, headers=headers)
    assert applied.status_code == 200
    assert applied.json() == {"status": "ACKNOWLEDGED"}
    replay = callback_client.post(url, json={**body, "outcome": "DUPLICATE"}, headers=headers)
    assert replay.status_code == 200
    assert replay.json() == {"status": "ACKNOWLEDGED"}
    with Session(migrated_engine) as session:
        target = session.get(DeletionTarget, target_id)
        assert target is not None and target.status == "ACKNOWLEDGED"

    with migrated_engine.begin() as connection:
        connection.execute(
            text("UPDATE deletion_targets SET ack_event_id=:event_id WHERE id=:id"),
            {"event_id": uuid4(), "id": target_id},
        )
    conflict = callback_client.post(url, json=body, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json() == {"code": "W2_DELETION_V2_CONFLICT"}
