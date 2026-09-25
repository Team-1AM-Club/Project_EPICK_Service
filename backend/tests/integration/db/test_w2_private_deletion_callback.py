from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
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


def test_authenticated_w2_v2_ack_is_bound_to_current_target(
    migrated_engine: Engine, callback_client: TestClient
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
        owner_id, target_id = owner.id, target.id
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
    assert (
        callback_client.post(
            url, json={**body, "owner_user_id": str(target_id)}, headers=headers
        ).status_code
        == 409
    )
    applied = callback_client.post(url, json=body, headers=headers)
    assert applied.status_code == 200
    assert applied.json() == {"status": "ACKNOWLEDGED"}
    replay = callback_client.post(url, json={**body, "outcome": "DUPLICATE"}, headers=headers)
    assert replay.status_code == 200
    with Session(migrated_engine) as session:
        target = session.get(DeletionTarget, target_id)
        assert target is not None and target.status == "ACKNOWLEDGED"
