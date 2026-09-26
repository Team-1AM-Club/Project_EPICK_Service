from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models.application_workspace import ApplicationProject
from app.models.identity import User
from app.models.jobs import OutboxMessage
from app.models.w2_commit_operations import W2CommitOperation
from app.runtime.lookup_adapter import create_lookup_app
from app.runtime.w2_commit_gate_worker import W2CommitGateAck, W2CommitGateAckWorker
from app.services.deletion import DeletionOrchestrationService
from app.services.jobs import JobService

RUNTIME_PRIVILEGES_SQL = Path(__file__).parents[3] / "infra" / "postgres" / "runtime_privileges.sql"
HEADERS = {
    "Authorization": "Bearer test-w2-bearer",
    "X-EPICK-Service-Principal": "w2",
}
URL = "/internal/v1/w2-private/gate-scope-lookup"


@pytest.fixture(autouse=True)
def clean_gate_scope_rows(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))


@pytest.fixture
def lookup_client(migrated_engine: Engine) -> Iterator[TestClient]:
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))
    lookup_engine = create_engine(migrated_engine.url.render_as_string(hide_password=False))

    @event.listens_for(lookup_engine, "checkout")
    def set_lookup_role(dbapi_connection, connection_record, connection_proxy) -> None:
        del connection_record, connection_proxy
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET ROLE epick_lookup")

    try:
        yield TestClient(
            create_lookup_app(
                session_factory=sessionmaker(bind=lookup_engine, expire_on_commit=False),
                expected_bearer_token="test-w2-bearer",
            )
        )
    finally:
        lookup_engine.dispose()


@pytest.mark.parametrize("project_scoped", [False, True])
@pytest.mark.parametrize("action", ["ABORT", "PURGE"])
def test_stage_less_terminal_gate_scope_is_derived_from_issued_w1_gate(
    migrated_engine: Engine,
    lookup_client: TestClient,
    project_scoped: bool,
    action: str,
) -> None:
    with Session(migrated_engine) as session:
        owner = User(display_name="Scope lookup owner", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        project = ApplicationProject(owner_user_id=owner.id) if project_scoped else None
        if project is not None:
            session.add(project)
            session.flush()
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            project_id=project.id if project is not None else None,
            job_type="SOURCE_COLLECTION",
            idempotency_key="stage-less-gate-scope",
            request_hash="a" * 64,
        )
        assert accepted.command is not None
        command = accepted.command
        command.command_type = "W2_SOURCE_COLLECTION"
        command.status = "INVALIDATED"
        job = accepted.job
        job.status = "CANCELLED"
        job.execution_fence = 2
        job.owner_deletion_epoch = 1
        owner.account_status = "ACTIVE" if project_scoped else "DELETION_PENDING"
        owner.deletion_epoch = 1
        if project is not None:
            project.status = "ARCHIVED"
        operation = W2CommitOperation(
            command_id=command.id,
            job_id=job.id,
            owner_user_id=owner.id,
            execution_fence=1,
            owner_deletion_epoch=0,
            purge_owner_deletion_epoch=1 if action == "PURGE" else None,
            result_digest="sha256:" + "a" * 64,
            operation_revision=2,
            state=f"{action}_PENDING",
        )
        session.add(operation)
        session.flush()
        session.add(
            OutboxMessage(
                message_type="w1.private.w2.commit-gate.v1",
                schema_version="w1.private.w2.commit-gate.v1",
                visibility_scope="PRIVATE",
                aggregate_type="W2_COMMIT_OPERATION",
                aggregate_id=operation.id,
                aggregate_revision=2,
                command_id=command.id,
                job_id=job.id,
                owner_user_id=owner.id,
                execution_fence=1,
                owner_deletion_epoch=0,
                payload={
                    "operation_id": str(operation.id),
                    "operation_revision": 2,
                    "action": action,
                    "result_digest": "sha256:" + "a" * 64,
                    **({"purge_owner_deletion_epoch": 1} if action == "PURGE" else {}),
                },
            )
        )
        body = {
            "schema_version": "w1.private.w2-gate-scope-lookup.v1",
            "owner_user_id": str(owner.id),
            "owner_deletion_epoch": 0,
            "command_id": str(command.id),
            "job_id": str(job.id),
            "execution_fence": 1,
            "operation_id": str(operation.id),
            "operation_revision": 2,
            "action": action,
            "phase": "APPLY",
            "result_digest": "sha256:" + "a" * 64,
            **({"purge_owner_deletion_epoch": 1} if action == "PURGE" else {}),
        }
        scope = (
            {"type": "PROJECT", "project_id": str(project.id)}
            if project is not None
            else {"type": "ACCOUNT"}
        )
        operation_id = operation.id
        session.commit()

    assert lookup_client.post(URL, json=body).status_code == 401
    assert (
        lookup_client.post(
            URL, json=body, headers={**HEADERS, "X-EPICK-Service-Principal": "w3"}
        ).status_code
        == 403
    )
    response = lookup_client.post(URL, json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json() == {**body, "scope": scope}
    assert "authority_ref" not in response.json()
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/gate-authority",
            json={**body, "schema_version": "w1.private.w2-gate-authority.v1", "scope": scope},
            headers=HEADERS,
        ).status_code
        == 200
    )
    for change in (
        {"owner_user_id": str(uuid4())},
        {"command_id": str(uuid4())},
        {"job_id": str(uuid4())},
        {"execution_fence": 2},
        {"operation_revision": 3},
        {"result_digest": "sha256:" + "b" * 64},
        {"action": "FINALIZE"},
    ):
        expected = 422 if action == "PURGE" and change == {"action": "FINALIZE"} else 403
        assert (
            lookup_client.post(URL, json={**body, **change}, headers=HEADERS).status_code
            == expected
        )

    # W2 can replay the original ACK after owner deletion without regenerating an ID or stage.
    ack = W2CommitGateAck(
        message_id=uuid4(),
        operation_id=operation_id,
        operation_revision=2,
        action=action,
        command_id=UUID(body["command_id"]),
        job_id=UUID(body["job_id"]),
        execution_fence=1,
        owner_deletion_epoch=0,
        purge_owner_deletion_epoch=1 if action == "PURGE" else None,
        result_digest="sha256:" + "a" * 64,
        payload_digest="sha256:" + "c" * 64,
    )
    ack_worker = W2CommitGateAckWorker(
        session_factory=sessionmaker(bind=migrated_engine, expire_on_commit=False)
    )
    assert ack_worker.apply_ack(ack=ack).outcome_code == "APPLIED"
    assert ack_worker.apply_ack(ack=ack).outcome_code == "DUPLICATE"
    with migrated_engine.connect() as connection:
        receipt = connection.execute(
            text(
                "SELECT outcome_code, payload_digest FROM inbox_receipts "
                "WHERE consumer_name = 'w1.w2-commit-gate-ack' AND event_id = :message_id"
            ),
            {"message_id": ack.message_id},
        ).one()
    assert receipt == ("APPLIED", ack.payload_digest)
    assert (
        ack_worker.apply_ack(ack=replace(ack, payload_digest="sha256:" + "d" * 64)).outcome_code
        == "ID_CONFLICT"
    )
    assert (
        lookup_client.post(URL, json={**body, "phase": "ACK_RELAY"}, headers=HEADERS).status_code
        == 200
    )

    # A gate scope lookup is never an authority grant and must not work without issuance proof.
    with migrated_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM outbox_messages WHERE aggregate_id = :operation_id"),
            {"operation_id": operation_id},
        )
    assert lookup_client.post(URL, json=body, headers=HEADERS).status_code == 403


def test_gate_scope_lookup_database_error_is_retryable_and_opaque() -> None:
    session_factory = Mock()
    session_factory.begin.side_effect = SQLAlchemyError("sensitive database failure")
    client = TestClient(
        create_lookup_app(session_factory=session_factory, expected_bearer_token="test-w2-bearer")
    )
    response = client.post(
        URL,
        json={
            "schema_version": "w1.private.w2-gate-scope-lookup.v1",
            "owner_user_id": str(uuid4()),
            "owner_deletion_epoch": 0,
            "command_id": str(uuid4()),
            "job_id": str(uuid4()),
            "execution_fence": 1,
            "operation_id": str(uuid4()),
            "operation_revision": 1,
            "action": "ABORT",
            "phase": "APPLY",
            "result_digest": "sha256:" + "c" * 64,
        },
        headers=HEADERS,
    )
    assert response.status_code == 503
    assert response.json()["code"] == "INTERNAL_RETRYABLE"
    assert "sensitive" not in response.text


@pytest.mark.parametrize("project_scoped", [False, True])
def test_actual_w1_deletion_issues_a_stage_less_abort_with_lookup_scope(
    migrated_engine: Engine,
    lookup_client: TestClient,
    project_scoped: bool,
) -> None:
    with Session(migrated_engine) as session:
        owner = User(display_name="Deletion gate owner", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        project = ApplicationProject(owner_user_id=owner.id) if project_scoped else None
        if project is not None:
            session.add(project)
            session.flush()
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            project_id=project.id if project is not None else None,
            job_type="SOURCE_COLLECTION",
            idempotency_key="deletion-gate-scope",
            request_hash="b" * 64,
        )
        assert accepted.command is not None
        accepted.command.command_type = "W2_SOURCE_COLLECTION"
        operation = W2CommitOperation(
            command_id=accepted.command.id,
            job_id=accepted.job.id,
            owner_user_id=owner.id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest="sha256:" + "e" * 64,
            operation_revision=1,
            state="PREPARE_PENDING",
        )
        session.add(operation)
        session.flush()
        deletion = DeletionOrchestrationService(session)
        if project is not None:
            preview = deletion.create_project_deletion_preview(
                owner_user_id=owner.id,
                project_id=project.id,
                preview_token="stage-less-project-delete",
            )
            deletion.confirm_and_start_project_deletion(
                owner_user_id=owner.id,
                deletion_request_id=preview.request.id,
                preview_token=preview.preview_token,
            )
        else:
            preview = deletion.create_account_deletion_preview(
                owner_user_id=owner.id, preview_token="stage-less-account-delete"
            )
            deletion.confirm_and_start_account_deletion(
                owner_user_id=owner.id,
                deletion_request_id=preview.request.id,
                preview_token=preview.preview_token,
            )
        session.flush()
        assert (operation.state, operation.operation_revision) == ("ABORT_PENDING", 2)
        body = {
            "schema_version": "w1.private.w2-gate-scope-lookup.v1",
            "owner_user_id": str(owner.id),
            "owner_deletion_epoch": 0,
            "command_id": str(accepted.command.id),
            "job_id": str(accepted.job.id),
            "execution_fence": 1,
            "operation_id": str(operation.id),
            "operation_revision": 2,
            "action": "ABORT",
            "phase": "APPLY",
            "result_digest": "sha256:" + "e" * 64,
        }
        expected_scope = (
            {"type": "PROJECT", "project_id": str(project.id)}
            if project is not None
            else {"type": "ACCOUNT"}
        )
        session.commit()

    response = lookup_client.post(URL, json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["scope"] == expected_scope
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/gate-authority",
            json={
                **body,
                "schema_version": "w1.private.w2-gate-authority.v1",
                "scope": expected_scope,
            },
            headers=HEADERS,
        ).status_code
        == 200
    )
