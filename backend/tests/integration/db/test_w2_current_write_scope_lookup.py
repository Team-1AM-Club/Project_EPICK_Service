from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.application_workspace import ApplicationProject
from app.models.identity import User
from app.models.jobs import Job, JobCommand, JobExecutionLease, OwnerExecutionSlot
from app.runtime.lookup_adapter import create_lookup_app
from app.services.jobs import JobService

RUNTIME_PRIVILEGES_SQL = Path(__file__).parents[3] / "infra" / "postgres" / "runtime_privileges.sql"
URL = "/internal/v1/w2-private/current-write-scope-lookup"
HEADERS = {
    "Authorization": "Bearer test-w2-bearer",
    "X-EPICK-Service-Principal": "w2",
}


@pytest.fixture(autouse=True)
def clean_scope_rows(migrated_engine: Engine) -> None:
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


def _seed_command(
    engine: Engine, *, command_type: str, project_scoped: bool
) -> tuple[dict[str, object], dict[str, str]]:
    with Session(engine) as session:
        owner = User(display_name="First W2 write", locale="ko-KR", timezone="Asia/Seoul")
        other_owner = User(display_name="Other owner", locale="ko-KR", timezone="Asia/Seoul")
        session.add_all((owner, other_owner))
        session.flush()
        project = ApplicationProject(owner_user_id=owner.id) if project_scoped else None
        if project is not None:
            session.add(project)
            session.flush()
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            project_id=project.id if project is not None else None,
            job_type=(
                "SOURCE_REGISTRATION"
                if command_type == "W2_DIRECT_SOURCE_REGISTRATION"
                else "SOURCE_COLLECTION"
            ),
            idempotency_key=f"first-write-{command_type}-{project_scoped}",
            request_hash="a" * 64,
        )
        assert accepted.command is not None
        accepted.command.command_type = command_type
        accepted.command.status = "ENQUEUED"
        accepted.job.status = "RUNNING"
        lease_id = uuid4()
        now = datetime.now(UTC)
        accepted.job.active_lease_id = lease_id
        session.add(
            JobExecutionLease(
                id=lease_id,
                job_id=accepted.job.id,
                owner_user_id=owner.id,
                slot_no=1,
                execution_fence=1,
                owner_deletion_epoch=0,
                claimed_at=now,
            )
        )
        slot = session.get(OwnerExecutionSlot, (owner.id, 1))
        assert slot is not None
        slot.job_id = accepted.job.id
        slot.lease_id = lease_id
        slot.claimed_at = now
        accepted.command.payload = {
            "command_type": command_type,
            "w2_command": {
                "schema_version": "w2.collection.v1",
                "command_id": str(accepted.command.id),
                "job_id": str(accepted.job.id),
                "authenticated_owner_ref": str(owner.id),
                "execution_fence": "1",
                "owner_deletion_epoch": 0,
                "project_ref": str(project.id) if project is not None else None,
            },
        }
        body: dict[str, object] = {
            "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
            "owner_user_id": str(owner.id),
            "owner_deletion_epoch": 0,
            "command_id": str(accepted.command.id),
            "job_id": str(accepted.job.id),
            "execution_fence": 1,
        }
        scope = (
            {"type": "PROJECT", "project_id": str(project.id)}
            if project is not None
            else {"type": "ACCOUNT"}
        )
        session.commit()
    return body, scope


@pytest.mark.parametrize("command_type", ["W2_SOURCE_COLLECTION", "W2_DIRECT_SOURCE_REGISTRATION"])
@pytest.mark.parametrize("project_scoped", [False, True])
def test_first_write_scope_is_exact_stable_and_not_a_permission(
    migrated_engine: Engine,
    lookup_client: TestClient,
    command_type: str,
    project_scoped: bool,
) -> None:
    body, scope = _seed_command(
        migrated_engine, command_type=command_type, project_scoped=project_scoped
    )
    assert lookup_client.post(URL, json=body).status_code == 401
    response = lookup_client.post(URL, json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json() == {**body, "scope": scope}
    assert lookup_client.post(URL, json=body, headers=HEADERS).json() == response.json()
    assert "authority_ref" not in response.json()
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/authority",
            json={**body, "schema_version": "w1.private.w2-write-authority.v1", "scope": scope},
            headers=HEADERS,
        ).status_code
        == 200
    )
    for change in (
        {"owner_user_id": str(uuid4())},
        {"owner_deletion_epoch": 1},
        {"command_id": str(uuid4())},
        {"job_id": str(uuid4())},
        {"execution_fence": 2},
    ):
        assert lookup_client.post(URL, json={**body, **change}, headers=HEADERS).status_code == 403
    assert (
        lookup_client.post(URL, json={**body, "scope": scope}, headers=HEADERS).status_code == 422
    )

    # A corrupted durable dispatch must not turn a null ref into ACCOUNT or
    # redirect a Project command to another or malformed Project UUID.
    with Session(migrated_engine) as session:
        command = session.get(JobCommand, UUID(str(body["command_id"])))
        assert command is not None
        payload = deepcopy(command.payload)
        w2_payload = payload["w2_command"]
        assert isinstance(w2_payload, dict)
        w2_payload["project_ref"] = None if project_scoped else str(uuid4())
        command.payload = payload
        session.commit()
    assert lookup_client.post(URL, json=body, headers=HEADERS).status_code == 403


@pytest.mark.parametrize("command_type", ["W2_SOURCE_COLLECTION", "W2_DIRECT_SOURCE_REGISTRATION"])
@pytest.mark.parametrize("project_scoped", [False, True])
def test_scope_lookup_fails_closed_after_cancel_or_deletion(
    migrated_engine: Engine,
    lookup_client: TestClient,
    command_type: str,
    project_scoped: bool,
) -> None:
    body, scope = _seed_command(
        migrated_engine, command_type=command_type, project_scoped=project_scoped
    )
    assert lookup_client.post(URL, json=body, headers=HEADERS).json()["scope"] == scope
    with Session(migrated_engine) as session:
        job = session.get(Job, UUID(str(body["job_id"])))
        assert job is not None
        job.status = "CANCELLED"
        session.commit()
    assert lookup_client.post(URL, json=body, headers=HEADERS).status_code == 403
    cleanup = {
        **body,
        "schema_version": "w1.private.w2-terminal-cleanup.v1",
        "scope": scope,
        "cleanup_kind": "RESERVATION_RELEASE",
    }
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/terminal-cleanup-authority",
            json=cleanup,
            headers=HEADERS,
        ).status_code
        == 200
    )
    with Session(migrated_engine) as session:
        job = session.get(Job, UUID(str(body["job_id"])))
        assert job is not None
        job.status = "RUNNING"
        owner = session.get(User, UUID(str(body["owner_user_id"])))
        assert owner is not None
        owner.deletion_epoch = 1
        owner.account_status = "DELETION_PENDING"
        session.commit()
    assert lookup_client.post(URL, json=body, headers=HEADERS).status_code == 403
    assert (
        lookup_client.post(
            "/internal/v1/w2-private/terminal-cleanup-authority",
            json=cleanup,
            headers=HEADERS,
        ).status_code
        == 200
    )


@pytest.mark.parametrize("project_scoped", [False, True])
@pytest.mark.parametrize("tamper_kind", ["missing", "other_project", "malformed"])
def test_stored_project_ref_must_match_w1_job_scope(
    migrated_engine: Engine,
    lookup_client: TestClient,
    project_scoped: bool,
    tamper_kind: str,
) -> None:
    body, _ = _seed_command(
        migrated_engine, command_type="W2_SOURCE_COLLECTION", project_scoped=project_scoped
    )
    with Session(migrated_engine) as session:
        command = session.get(JobCommand, UUID(str(body["command_id"])))
        assert command is not None
        payload = deepcopy(command.payload)
        w2_payload = payload["w2_command"]
        assert isinstance(w2_payload, dict)
        if tamper_kind == "missing":
            w2_payload.pop("project_ref")
        elif tamper_kind == "other_project":
            w2_payload["project_ref"] = str(uuid4())
        else:
            w2_payload["project_ref"] = "NOT_A_UUID"
        command.payload = payload
        session.commit()
    assert lookup_client.post(URL, json=body, headers=HEADERS).status_code == 403
