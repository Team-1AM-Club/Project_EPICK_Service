from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.application_workspace import ApplicationProject
from app.models.identity import User
from app.models.jobs import OutboxMessage
from app.models.w2_commit_operations import W2CommitOperation
from app.runtime.lookup_adapter import create_lookup_app
from app.services.deletion import DeletionOrchestrationService
from app.services.jobs import JobService

RUNTIME_PRIVILEGES_SQL = Path(__file__).parents[3] / "infra" / "postgres" / "runtime_privileges.sql"


@pytest.fixture(autouse=True)
def clean_authority_rows(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))


@pytest.fixture
def lookup_client(migrated_engine: Engine) -> Iterator[TestClient]:
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))
    lookup_engine = create_engine(
        migrated_engine.url.render_as_string(hide_password=False),
        pool_size=1,
        max_overflow=0,
    )

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


def test_w2_private_write_authority_requires_exact_current_project_binding(
    migrated_engine: Engine,
    lookup_client: TestClient,
) -> None:
    with Session(migrated_engine) as session:
        owner = User(display_name="W2 writer", locale="ko-KR", timezone="Asia/Seoul")
        other_owner = User(display_name="Other writer", locale="ko-KR", timezone="Asia/Seoul")
        session.add_all((owner, other_owner))
        session.flush()
        project = ApplicationProject(owner_user_id=owner.id)
        other_project = ApplicationProject(owner_user_id=owner.id)
        session.add_all((project, other_project))
        session.flush()
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            project_id=project.id,
            job_type="SOURCE_COLLECTION",
            idempotency_key="w2-authority",
            request_hash="a" * 64,
        )
        assert accepted.command is not None
        accepted.command.command_type = "W2_SOURCE_COLLECTION"
        accepted.command.status = "ENQUEUED"
        command_id = accepted.command.id
        job_id = accepted.job.id
        owner_id = owner.id
        other_owner_id = other_owner.id
        project_id = project.id
        other_project_id = other_project.id
        session.commit()

    client = lookup_client
    body = {
        "schema_version": "w1.private.w2-write-authority.v1",
        "owner_user_id": str(owner_id),
        "owner_deletion_epoch": 0,
        "command_id": str(command_id),
        "job_id": str(job_id),
        "execution_fence": 1,
        "scope": {"type": "PROJECT", "project_id": str(project_id)},
    }
    url = "/internal/v1/w2-private/authority"
    headers = {
        "Authorization": "Bearer test-w2-bearer",
        "X-EPICK-Service-Principal": "w2",
    }
    assert client.post(url, json=body).status_code == 401
    authorized = client.post(url, json=body, headers=headers)
    assert authorized.status_code == 200
    assert authorized.json() == {
        **body,
        "authority_ref": f"w1:command:{command_id}:fence:1:epoch:0",
    }
    for change in (
        {"owner_user_id": str(other_owner_id)},
        {"owner_deletion_epoch": 1},
        {"command_id": str(uuid4())},
        {"job_id": str(uuid4())},
        {"scope": {"type": "ACCOUNT"}},
        {"scope": {"type": "PROJECT", "project_id": str(other_project_id)}},
    ):
        assert client.post(url, json={**body, **change}, headers=headers).status_code == 403

    with Session(migrated_engine) as session:
        project = session.get(ApplicationProject, project_id)
        assert project is not None
        project.status = "ARCHIVED"
        session.commit()
    assert client.post(url, json=body, headers=headers).status_code == 403


def test_w2_private_write_authority_account_scope_rejects_missing_or_stale_proof(
    migrated_engine: Engine,
    lookup_client: TestClient,
) -> None:
    with Session(migrated_engine) as session:
        owner = User(display_name="Direct source owner", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            job_type="SOURCE_COLLECTION",
            idempotency_key="direct-authority",
            request_hash="b" * 64,
        )
        assert accepted.command is not None
        accepted.command.command_type = "W2_DIRECT_SOURCE_REGISTRATION"
        accepted.command.status = "ENQUEUED"
        owner_id = owner.id
        body = {
            "schema_version": "w1.private.w2-write-authority.v1",
            "owner_user_id": str(owner_id),
            "owner_deletion_epoch": 0,
            "command_id": str(accepted.command.id),
            "job_id": str(accepted.job.id),
            "execution_fence": 1,
            "scope": {"type": "ACCOUNT"},
        }
        session.commit()
    url = "/internal/v1/w2-private/authority"
    headers = {
        "Authorization": "Bearer test-w2-bearer",
        "X-EPICK-Service-Principal": "w2",
    }
    assert lookup_client.post(url, json=body, headers=headers).status_code == 200
    missing_scope = {key: value for key, value in body.items() if key != "scope"}
    assert lookup_client.post(url, json=missing_scope, headers=headers).status_code == 422
    stale_fence = {**body, "execution_fence": 2}
    assert lookup_client.post(url, json=stale_fence, headers=headers).status_code == 403
    with Session(migrated_engine) as session:
        owner = session.get(User, owner_id)
        assert owner is not None
        owner.deletion_epoch = 1
        session.commit()
    assert lookup_client.post(url, json=body, headers=headers).status_code == 403


@pytest.mark.parametrize("account", [False, True])
@pytest.mark.parametrize("writer_first", [False, True])
def test_w2_private_write_authority_is_current_only_across_deletion_order(
    migrated_engine: Engine,
    lookup_client: TestClient,
    account: bool,
    writer_first: bool,
) -> None:
    """A W2 write decision before deletion is never reusable after epoch advance."""
    with Session(migrated_engine) as session:
        owner = User(display_name="Ordered W2 writer", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        project = None if account else ApplicationProject(owner_user_id=owner.id)
        if project is not None:
            session.add(project)
            session.flush()
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            project_id=project.id if project is not None else None,
            job_type="SOURCE_COLLECTION",
            idempotency_key="w2-authority-deletion-order",
            request_hash="c" * 64,
        )
        assert accepted.command is not None
        accepted.command.command_type = "W2_SOURCE_COLLECTION"
        body = {
            "schema_version": "w1.private.w2-write-authority.v1",
            "owner_user_id": str(owner.id),
            "owner_deletion_epoch": 0,
            "command_id": str(accepted.command.id),
            "job_id": str(accepted.job.id),
            "execution_fence": 1,
            "scope": {"type": "ACCOUNT"}
            if account
            else {"type": "PROJECT", "project_id": str(project.id)},
        }
        owner_id = owner.id
        project_id = project.id if project is not None else None
        session.commit()

    url = "/internal/v1/w2-private/authority"
    headers = {
        "Authorization": "Bearer test-w2-bearer",
        "X-EPICK-Service-Principal": "w2",
    }
    if writer_first:
        assert lookup_client.post(url, json=body, headers=headers).status_code == 200

    with Session(migrated_engine) as session:
        deletion = DeletionOrchestrationService(session)
        if account:
            preview = deletion.create_account_deletion_preview(
                owner_user_id=owner_id, preview_token="ordered-account-preview"
            )
            deletion.confirm_and_start_account_deletion(
                owner_user_id=owner_id,
                deletion_request_id=preview.request.id,
                preview_token=preview.preview_token,
            )
        else:
            assert project_id is not None
            preview = deletion.create_project_deletion_preview(
                owner_user_id=owner_id,
                project_id=project_id,
                preview_token="ordered-project-preview",
            )
            deletion.confirm_and_start_project_deletion(
                owner_user_id=owner_id,
                deletion_request_id=preview.request.id,
                preview_token=preview.preview_token,
            )
        session.commit()

    assert lookup_client.post(url, json=body, headers=headers).status_code == 403


@pytest.mark.parametrize(
    ("action", "pending_state", "applied_state"),
    [
        ("ABORT", "ABORT_PENDING", "ABORTED"),
        ("PURGE", "PURGE_PENDING", "PURGED"),
    ],
)
def test_w2_gate_authority_permits_bound_cleanup_after_owner_deletion(
    migrated_engine: Engine,
    lookup_client: TestClient,
    action: str,
    pending_state: str,
    applied_state: str,
) -> None:
    with Session(migrated_engine) as session:
        owner = User(display_name="Gate owner", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            job_type="SOURCE_COLLECTION",
            idempotency_key="w2-gate-authority-abort",
            request_hash="d" * 64,
        )
        assert accepted.command is not None
        command = accepted.command
        command.command_type = "W2_SOURCE_COLLECTION"
        command.status = "INVALIDATED"
        job = accepted.job
        job.status = "CANCELLED"
        job.execution_fence = 2
        job.owner_deletion_epoch = 1
        owner.deletion_epoch = 1
        owner.account_status = "DELETION_PENDING"
        operation = W2CommitOperation(
            command_id=command.id,
            job_id=job.id,
            owner_user_id=owner.id,
            execution_fence=1,
            owner_deletion_epoch=0,
            purge_owner_deletion_epoch=1 if action == "PURGE" else None,
            result_digest="sha256:" + "a" * 64,
            operation_revision=2,
            state=pending_state,
        )
        session.add(operation)
        session.commit()
        body = {
            "schema_version": "w1.private.w2-gate-authority.v1",
            "phase": "APPLY",
            "action": action,
            "operation_id": str(operation.id),
            "operation_revision": 2,
            "owner_user_id": str(owner.id),
            "owner_deletion_epoch": 0,
            "command_id": str(command.id),
            "job_id": str(job.id),
            "execution_fence": 1,
            "scope": {"type": "ACCOUNT"},
            "result_digest": "sha256:" + "a" * 64,
        }
        if action == "PURGE":
            body["purge_owner_deletion_epoch"] = 1
    headers = {
        "Authorization": "Bearer test-w2-bearer",
        "X-EPICK-Service-Principal": "w2",
    }
    url = "/internal/v1/w2-private/gate-authority"
    result = lookup_client.post(url, json=body, headers=headers)
    assert result.status_code == 200
    assert lookup_client.post(url, json=body).status_code in {401, 403}
    assert result.json()["authority_ref"] == (
        f"w1:gate:{operation.id}:revision:2:action:{action}:phase:APPLY"
    )
    with Session(migrated_engine) as session:
        persisted = session.get(W2CommitOperation, operation.id)
        assert persisted is not None
        persisted.state = applied_state
        persisted.operation_revision = 3
        session.commit()
    assert (
        lookup_client.post(
            url,
            json={**body, "phase": "ACK_RELAY"},
            headers=headers,
        ).status_code
        == 200
    )
    with Session(migrated_engine) as session:
        persisted_owner = session.get(User, owner.id)
        assert persisted_owner is not None
        persisted_owner.deletion_epoch = 2
        session.commit()
    assert (
        lookup_client.post(url, json={**body, "phase": "ACK_RELAY"}, headers=headers).status_code
        == 200
    )
    assert lookup_client.post(url, json=body, headers=headers).status_code == 403
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/authority",
            json={
                key: value
                for key, value in body.items()
                if key
                in {
                    "owner_user_id",
                    "owner_deletion_epoch",
                    "command_id",
                    "job_id",
                    "execution_fence",
                    "scope",
                }
            }
            | {"schema_version": "w1.private.w2-write-authority.v1"},
            headers=headers,
        ).status_code
        == 403
    )
    for change in (
        {"operation_revision": 3},
        {"action": "PREPARE"},
        {"result_digest": "sha256:" + "b" * 64},
        {"owner_user_id": str(uuid4())},
    ):
        expected = 422 if action == "PURGE" and change == {"action": "PREPARE"} else 403
        response = lookup_client.post(url, json={**body, **change}, headers=headers)
        assert response.status_code == expected


def test_w2_gate_authority_rejects_forward_action_for_invalidated_command(
    migrated_engine: Engine,
    lookup_client: TestClient,
) -> None:
    with Session(migrated_engine) as session:
        owner = User(display_name="Invalidated gate owner", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            job_type="SOURCE_COLLECTION",
            idempotency_key="w2-gate-invalidated",
            request_hash="e" * 64,
        )
        assert accepted.command is not None
        command = accepted.command
        command.command_type = "W2_SOURCE_COLLECTION"
        command.status = "INVALIDATED"
        operation = W2CommitOperation(
            command_id=command.id,
            job_id=accepted.job.id,
            owner_user_id=owner.id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest="sha256:" + "c" * 64,
            operation_revision=1,
            state="PREPARE_PENDING",
        )
        session.add(operation)
        session.commit()
        body = {
            "schema_version": "w1.private.w2-gate-authority.v1",
            "phase": "APPLY",
            "action": "PREPARE",
            "operation_id": str(operation.id),
            "operation_revision": 1,
            "owner_user_id": str(owner.id),
            "owner_deletion_epoch": 0,
            "command_id": str(command.id),
            "job_id": str(accepted.job.id),
            "execution_fence": 1,
            "scope": {"type": "ACCOUNT"},
            "result_digest": "sha256:" + "c" * 64,
        }
    headers = {
        "Authorization": "Bearer test-w2-bearer",
        "X-EPICK-Service-Principal": "w2",
    }
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/gate-authority", json=body, headers=headers
        ).status_code
        == 403
    )
    with Session(migrated_engine) as session:
        persisted = session.get(W2CommitOperation, operation.id)
        assert persisted is not None
        persisted.state = "PREPARED"
        persisted.operation_revision = 2
        session.commit()
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/gate-authority",
            json={**body, "phase": "ACK_RELAY"},
            headers=headers,
        ).status_code
        == 200
    )
    with Session(migrated_engine) as session:
        persisted = session.get(W2CommitOperation, operation.id)
        assert persisted is not None
        persisted.state = "W1_COMMITTED"
        persisted.operation_revision = 3
        session.add(
            OutboxMessage(
                message_type="w1.private.w2.commit-gate.v1",
                schema_version="w1.private.w2.commit-gate.v1",
                visibility_scope="PRIVATE",
                aggregate_type="W2_COMMIT_OPERATION",
                aggregate_id=operation.id,
                aggregate_revision=1,
                command_id=command.id,
                job_id=accepted.job.id,
                owner_user_id=owner.id,
                execution_fence=1,
                owner_deletion_epoch=0,
                payload={
                    "operation_id": str(operation.id),
                    "operation_revision": 1,
                    "action": "PREPARE",
                    "result_digest": "sha256:" + "c" * 64,
                },
            )
        )
        session.commit()
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/gate-authority",
            json={**body, "phase": "ACK_RELAY"},
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/gate-authority",
            json={**body, "phase": "ACK_RELAY", "action": "FINALIZE"},
            headers=headers,
        ).status_code
        == 403
    )


def test_w2_gate_authority_rejects_prepare_for_archived_project(
    migrated_engine: Engine,
    lookup_client: TestClient,
) -> None:
    with Session(migrated_engine) as session:
        owner = User(display_name="Archived gate owner", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        project = ApplicationProject(owner_user_id=owner.id)
        session.add(project)
        session.flush()
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            project_id=project.id,
            job_type="SOURCE_COLLECTION",
            idempotency_key="w2-gate-archived-project",
            request_hash="f" * 64,
        )
        assert accepted.command is not None
        accepted.command.command_type = "W2_SOURCE_COLLECTION"
        project.status = "ARCHIVED"
        operation = W2CommitOperation(
            command_id=accepted.command.id,
            job_id=accepted.job.id,
            owner_user_id=owner.id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest="sha256:" + "d" * 64,
            operation_revision=1,
            state="PREPARE_PENDING",
        )
        session.add(operation)
        session.commit()
        body = {
            "schema_version": "w1.private.w2-gate-authority.v1",
            "phase": "APPLY",
            "action": "PREPARE",
            "operation_id": str(operation.id),
            "operation_revision": 1,
            "owner_user_id": str(owner.id),
            "owner_deletion_epoch": 0,
            "command_id": str(accepted.command.id),
            "job_id": str(accepted.job.id),
            "execution_fence": 1,
            "scope": {"type": "PROJECT", "project_id": str(project.id)},
            "result_digest": "sha256:" + "d" * 64,
        }
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/gate-authority",
            json=body,
            headers={
                "Authorization": "Bearer test-w2-bearer",
                "X-EPICK-Service-Principal": "w2",
            },
        ).status_code
        == 403
    )
