from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.application_workspace import ApplicationProject, Company
from app.models.deletion import DeletionTarget
from app.models.identity import User
from app.models.sources import JobSourceLink, Source, SourceCollectionAttempt
from app.models.w2_commit_operations import W2CommitOperation, W2StagedResult
from app.services.deletion import (
    DeletionOrchestrationService,
    DeletionTransitionError,
    StaleDeletionAcknowledgementError,
)
from app.services.jobs import JobService

RUNTIME_PRIVILEGES_SQL = Path(__file__).parents[3] / "infra" / "postgres" / "runtime_privileges.sql"


@pytest.fixture(autouse=True)
def clean_rows(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))


@pytest.mark.parametrize("account", [False, True])
def test_v2_ack_purges_only_matching_private_links(db_session: Session, account: bool) -> None:
    owner = User(display_name="Delete project", locale="ko-KR", timezone="Asia/Seoul")
    other = User(display_name="Keep owner", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add_all((owner, other))
    db_session.flush()
    project = ApplicationProject(owner_user_id=owner.id)
    keep_project = ApplicationProject(owner_user_id=owner.id)
    other_project = ApplicationProject(owner_user_id=other.id)
    db_session.add_all((project, keep_project, other_project))
    company = Company(legal_name="Public", display_name="Public")
    db_session.add(company)
    db_session.flush()
    source = Source(
        company_id=company.id,
        source_type="OFFICIAL",
        canonical_url="https://public.example/shared-v2",
        canonical_url_hash="shared-v2",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    db_session.add(source)
    db_session.flush()
    jobs = JobService(db_session)
    links = []
    accepted_jobs = []
    for index, (job_owner, job_project) in enumerate(
        ((owner, project), (owner, keep_project), (other, other_project))
    ):
        accepted = jobs.accept_job(
            owner_user_id=job_owner.id,
            project_id=job_project.id,
            job_type="SOURCE_COLLECTION",
            idempotency_key=f"v2-purge-{index}",
            request_hash=str(index) * 64,
        )
        link = JobSourceLink(
            job_id=accepted.job.id,
            owner_user_id=job_owner.id,
            source_id=source.id,
            command_id=accepted.command.id if index == 0 else None,
            purpose_ref=f"v2-test-{index}",
        )
        db_session.add(link)
        links.append(link)
        accepted_jobs.append(accepted)
    db_session.flush()
    first = accepted_jobs[0]
    assert first.command is not None
    attempt = SourceCollectionAttempt(
        source_id=source.id,
        job_source_link_id=links[0].id,
        command_id=first.command.id,
        idempotency_key="public-observation-v2",
        attempt_no=1,
        access_result="ALLOWED",
        storage_result="STORED_METADATA",
        parse_result="SUCCEEDED",
        policy_version="policy-v1",
        started_at=datetime.now(UTC),
        command_schema_version="v1",
    )
    operation = W2CommitOperation(
        command_id=first.command.id,
        job_id=first.job.id,
        owner_user_id=owner.id,
        execution_fence=first.job.execution_fence,
        owner_deletion_epoch=0,
        result_digest="sha256:" + "a" * 64,
    )
    db_session.add_all((attempt, operation))
    db_session.flush()
    staged = W2StagedResult(
        operation_id=operation.id,
        command_id=first.command.id,
        owner_user_id=owner.id,
        origin_message_id=uuid4(),
        schema_version="w2.collection-result.v1",
        producer_name="w2",
        occurred_at=datetime.now(UTC),
        payload_digest="sha256:" + "b" * 64,
        result_digest="sha256:" + "a" * 64,
        result_payload={"private": "candidate"},
        payload_state="ACTIVE",
    )
    db_session.add(staged)
    db_session.flush()

    deletion = DeletionOrchestrationService(db_session)
    if account:
        preview = deletion.create_account_deletion_preview(
            owner_user_id=owner.id, preview_token="v2-account-preview"
        )
        request = deletion.confirm_and_start_account_deletion(
            owner_user_id=owner.id,
            deletion_request_id=preview.request.id,
            preview_token=preview.preview_token,
        )
    else:
        preview = deletion.create_project_deletion_preview(
            owner_user_id=owner.id, project_id=project.id, preview_token="v2-project-preview"
        )
        request = deletion.confirm_and_start_project_deletion(
            owner_user_id=owner.id,
            deletion_request_id=preview.request.id,
            preview_token=preview.preview_token,
        )
    target = db_session.scalar(
        select(DeletionTarget).where(
            DeletionTarget.deletion_request_id == request.id,
            DeletionTarget.store_type == "W2_SOURCE_RUNTIME",
        )
    )
    assert target is not None
    link_ids = tuple(link.id for link in links)
    target_id = target.id
    deletion.mark_target_dispatched(
        owner_user_id=owner.id,
        deletion_request_id=request.id,
        deletion_target_id=target.id,
    )

    with pytest.raises(DeletionTransitionError):
        deletion.acknowledge_target(
            owner_user_id=owner.id,
            deletion_request_id=request.id,
            deletion_target_id=target.id,
            ack_epoch=1,
            ack_event_id=uuid4(),
        )
    ack = json.dumps(
        {
            "schema_version": "w2.private-deletion-ack.v2",
            "deletion_id": str(target.id),
            "owner_user_id": str(owner.id),
            "deletion_epoch": 1,
            "scope": {"type": "ACCOUNT"}
            if account
            else {"type": "PROJECT", "project_id": str(project.id)},
            "outcome": "APPLIED",
        }
    )
    with pytest.raises(StaleDeletionAcknowledgementError):
        deletion.apply_w2_private_deletion_ack_v2(
            owner_user_id=owner.id,
            deletion_request_id=request.id,
            deletion_target_id=target.id,
            ack_body=ack.replace('"deletion_epoch": 1', '"deletion_epoch": 2'),
        )
    assert db_session.get(JobSourceLink, links[0].id) is not None

    db_session.execute(
        text(
            "CREATE FUNCTION test_fail_w2_private_link_delete() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN IF current_setting('app.test_fail_w2_clear', true) = '1' THEN "
            "RAISE EXCEPTION 'injected private link failure'; END IF; RETURN OLD; END; $$"
        )
    )
    db_session.execute(
        text(
            "CREATE TRIGGER test_fail_w2_private_link_delete BEFORE DELETE ON job_source_links "
            "FOR EACH ROW EXECUTE FUNCTION test_fail_w2_private_link_delete()"
        )
    )
    db_session.execute(text("SET LOCAL ROLE epick_deleter"))

    with pytest.raises(DBAPIError, match="injected private link failure"):
        with db_session.begin_nested():
            db_session.execute(text("SELECT set_config('app.test_fail_w2_clear', '1', true)"))
            deletion.apply_w2_private_deletion_ack_v2(
                owner_user_id=owner.id,
                deletion_request_id=request.id,
                deletion_target_id=target.id,
                ack_body=ack,
            )
    db_session.execute(text("SELECT set_config('app.test_fail_w2_clear', '0', true)"))
    db_session.execute(text("RESET ROLE"))
    assert (
        db_session.scalar(
            text("SELECT count(*) FROM job_source_links WHERE id = :id"), {"id": link_ids[0]}
        )
        == 1
    )
    assert (
        db_session.scalar(
            text("SELECT status FROM deletion_targets WHERE id = :id"), {"id": target_id}
        )
        != "ACKNOWLEDGED"
    )

    db_session.execute(text("SET LOCAL ROLE epick_deleter"))
    deletion.apply_w2_private_deletion_ack_v2(
        owner_user_id=owner.id,
        deletion_request_id=request.id,
        deletion_target_id=target.id,
        ack_body=ack,
    )
    db_session.flush()
    db_session.execute(text("RESET ROLE"))
    assert db_session.get(JobSourceLink, link_ids[0]) is None
    assert (db_session.get(JobSourceLink, link_ids[1]) is None) == account
    assert db_session.get(JobSourceLink, link_ids[2]) is not None
    assert db_session.get(Source, source.id) is not None
    db_session.refresh(attempt)
    db_session.refresh(staged)
    assert (attempt.job_source_link_id, attempt.command_id) == (None, None)
    assert staged.payload_state == "CLEARED"
    assert staged.result_payload is None
    assert target.status == "ACKNOWLEDGED"
    assert target.ack_epoch == 1

    deletion.apply_w2_private_deletion_ack_v2(
        owner_user_id=owner.id,
        deletion_request_id=request.id,
        deletion_target_id=target.id,
        ack_body=ack.replace('"APPLIED"', '"DUPLICATE"'),
    )
    assert target.status == "ACKNOWLEDGED"
