from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.identity import User
from app.models.jobs import Job, JobCommand
from app.models.sources import JobSourceLink
from app.runtime.w4_question_core_context import (
    issue_w4_question_core_context,
    resolve_w4_question_core_context,
)
from app.runtime.w4_question_core_context_adapter import create_w4_question_core_context_app
from tests.integration.db.w4_question_core_support import seed_question_job

BACKEND_ROOT = Path(__file__).parents[3]
RUNTIME_PRIVILEGES_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_privileges.sql"


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer w4-context-test-token",
        "X-EPICK-Service-Principal": "w4",
    }


@pytest.mark.postgres
def test_w4_context_source_remains_active_with_historical_w2_retry_links(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))

    with db_session.begin():
        owner, _, source, job, question_version, _ = seed_question_job(db_session)
        commands = [
            JobCommand(
                job_id=job.id,
                owner_user_id=owner.id,
                command_type="W2_SOURCE_COLLECTION",
                command_schema_version="w2.collection.v1",
                command_sequence=sequence,
                execution_fence=sequence,
                owner_deletion_epoch=0,
                analysis_input_version=job.analysis_input_version,
                payload={},
                status="CONSUMED",
            )
            for sequence in (1, 2)
        ]
        db_session.add_all(commands)
        db_session.flush()
        first_link = db_session.scalar(
            select(JobSourceLink).where(JobSourceLink.job_id == job.id)
        )
        assert first_link is not None
        first_link.command_id = commands[0].id
        db_session.add(
            JobSourceLink(
                job_id=job.id,
                owner_user_id=owner.id,
                source_id=source.id,
                command_id=commands[1].id,
                purpose_ref=first_link.purpose_ref,
                analysis_input_version=job.analysis_input_version,
            )
        )
        context = issue_w4_question_core_context(
            session=db_session,
            job=job,
            question_version_id=question_version.id,
            source_id=source.id,
            valid_for=timedelta(minutes=5),
        )
        resolved = resolve_w4_question_core_context(
            session=db_session, context_key=context.context_key
        )
        assert resolved is not None
        assert resolved.source_active is True


@pytest.mark.postgres
def test_w4_context_adapter_uses_opaque_handle_and_fails_closed_on_currentness_change(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))

    with db_session.begin():
        owner, _, source, job, question_version, _ = seed_question_job(db_session)
        context = issue_w4_question_core_context(
            session=db_session,
            job=job,
            question_version_id=question_version.id,
            source_id=source.id,
            valid_for=timedelta(minutes=5),
        )
        context_key = context.context_key
        job_id = job.id
        owner_id = owner.id

    context_engine = create_engine(
        migrated_engine.url.render_as_string(hide_password=False),
        pool_size=1,
        max_overflow=0,
    )
    context_sessions = sessionmaker(
        bind=context_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    @event.listens_for(context_engine, "checkout")
    def set_w4_context_role(dbapi_connection, connection_record, connection_proxy) -> None:
        del connection_record, connection_proxy
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET ROLE epick_w4_context")

    try:
        client = TestClient(
            create_w4_question_core_context_app(
                session_factory=context_sessions,
                expected_bearer_token="w4-context-test-token",
            )
        )
        response = client.post(
            "/internal/v1/w4/question-core-contexts/resolve",
            json={
                "schema_version": "w1.private.w4-question-core-context.v1",
                "context_key": str(context_key),
            },
            headers=_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["context_key"] == str(context_key)
        assert body["job_id"] == str(job_id)
        assert body["processing_allowed"] is True
        assert body["question_current"] is True
        assert body["source_active"] is True
        assert body["revoked"] is False
        assert body["data_kind"] == "SYNTHETIC"
        assert body["current_decision_version"] == 0
        assert set(body).isdisjoint({"owner_user_id", "company_id", "prompt", "canonical_url"})
        first_revision = body["authorization_revision"]

        # Advance only the current owner epoch.  The context and Job retain the
        # old binding on purpose: the first resolve after an owner-side deletion
        # change must already revoke W4 send permission.
        with db_session.begin():
            owner = db_session.get(User, owner_id)
            assert owner is not None
            owner.deletion_epoch += 1

        owner_epoch_revoked = client.post(
            "/internal/v1/w4/question-core-contexts/resolve",
            json={
                "schema_version": "w1.private.w4-question-core-context.v1",
                "context_key": str(context_key),
            },
            headers=_headers(),
        )
        assert owner_epoch_revoked.status_code == 200
        assert owner_epoch_revoked.json()["processing_allowed"] is False
        assert owner_epoch_revoked.json()["revoked"] is True
        assert owner_epoch_revoked.json()["authorization_revision"] != first_revision

        with db_session.begin():
            job = db_session.get(Job, job_id)
            assert job is not None and job.owner_user_id == owner_id
            job.status = "CANCEL_REQUESTED"

        revoked = client.post(
            "/internal/v1/w4/question-core-contexts/resolve",
            json={
                "schema_version": "w1.private.w4-question-core-context.v1",
                "context_key": str(context_key),
            },
            headers=_headers(),
        )
        assert revoked.status_code == 200
        assert revoked.json()["processing_allowed"] is False
        assert revoked.json()["revoked"] is True
        assert revoked.json()["authorization_revision"] != first_revision

        unknown = client.post(
            "/internal/v1/w4/question-core-contexts/resolve",
            json={
                "schema_version": "w1.private.w4-question-core-context.v1",
                "context_key": str(uuid4()),
            },
            headers=_headers(),
        )
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "W4_CONTEXT_NOT_FOUND"
        unauthenticated = client.post(
            "/internal/v1/w4/question-core-contexts/resolve",
            json={
                "schema_version": "w1.private.w4-question-core-context.v1",
                "context_key": str(context_key),
            },
        )
        assert unauthenticated.status_code == 401
    finally:
        context_engine.dispose()


@pytest.mark.postgres
def test_w4_context_issuer_rejects_real_data_until_policy_is_approved(
    db_session: Session,
) -> None:
    with db_session.begin():
        _, _, source, job, question_version, _ = seed_question_job(db_session)
        with pytest.raises(ValueError, match="REAL_CONTEXT_DISABLED"):
            issue_w4_question_core_context(
                session=db_session,
                job=job,
                question_version_id=question_version.id,
                source_id=source.id,
                valid_for=timedelta(minutes=5),
                data_kind="REAL",
            )
