from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session

from app.models.application_workspace import Company
from app.models.identity import User
from app.models.jobs import Job
from app.models.sources import JobSourceLink, Source
from app.services.w3_authority import W3AuthorityNotFoundError, W3AuthorityService


@pytest.fixture(autouse=True)
def clean_authority_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))


def _seed_currentness(session: Session, *, suffix: str = "current"):
    owner = User(
        display_name=f"Authority owner {suffix}",
        locale="ko-KR",
        timezone="Asia/Seoul",
    )
    company = Company(
        legal_name=f"Authority Company {suffix}",
        display_name=f"Authority Company {suffix}",
    )
    session.add_all([owner, company])
    session.flush()

    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url=f"https://example.test/{suffix}",
        canonical_url_hash=f"authority-{suffix}-{uuid4()}",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    job = Job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        status="RUNNING",
        dispatch_status="CLAIMED",
        owner_deletion_epoch=0,
        analysis_input_version=f"analysis:{suffix}:v1",
    )
    session.add_all([source, job])
    session.flush()
    link = JobSourceLink(
        job_id=job.id,
        owner_user_id=owner.id,
        source_id=source.id,
        source_version_id=None,
        command_id=None,
        purpose_ref="W3_CORE_DECISION",
        analysis_input_version=job.analysis_input_version,
    )
    session.add(link)
    session.flush()
    return owner, company, source, job, link


@pytest.mark.postgres
def test_current_authority_is_derived_from_the_bound_owner_job_and_source(
    db_session: Session,
) -> None:
    owner, company, source, job, _ = _seed_currentness(db_session)

    authorization = W3AuthorityService(db_session).current(job_id=job.id, source_id=source.id)

    assert authorization.context.job_id == job.id
    assert authorization.context.company_id == company.id
    assert authorization.context.source_id == source.id
    assert authorization.context.analysis_input_version == job.analysis_input_version
    assert authorization.owner_id == owner.id
    assert authorization.owner_epoch == owner.deletion_epoch == job.owner_deletion_epoch
    assert authorization.active is True


@pytest.mark.postgres
def test_unbound_or_other_company_source_is_not_authoritative(db_session: Session) -> None:
    _, _, _, job, _ = _seed_currentness(db_session, suffix="job")
    _, _, other_source, _, _ = _seed_currentness(db_session, suffix="other")

    with pytest.raises(W3AuthorityNotFoundError, match="AUTHORITY_CONTEXT_NOT_FOUND"):
        W3AuthorityService(db_session).current(job_id=job.id, source_id=other_source.id)


@pytest.mark.postgres
@pytest.mark.parametrize("terminal_status", ["CANCEL_REQUESTED", "CANCELLED", "FAILED_FINAL"])
def test_cancelled_or_terminal_job_returns_inactive_authorization(
    db_session: Session,
    terminal_status: str,
) -> None:
    _, _, source, job, _ = _seed_currentness(db_session, suffix=terminal_status.lower())
    job.status = terminal_status
    db_session.flush()

    authorization = W3AuthorityService(db_session).current(job_id=job.id, source_id=source.id)

    assert authorization.active is False


@pytest.mark.postgres
def test_owner_deletion_epoch_change_returns_inactive_authorization(db_session: Session) -> None:
    owner, _, source, job, _ = _seed_currentness(db_session, suffix="deleted")
    owner.deletion_epoch += 1
    owner.account_status = "DELETION_PENDING"
    db_session.flush()

    authorization = W3AuthorityService(db_session).current(job_id=job.id, source_id=source.id)

    assert authorization.owner_epoch == owner.deletion_epoch
    assert authorization.active is False


@pytest.mark.postgres
def test_stale_linked_analysis_input_returns_inactive_authorization(db_session: Session) -> None:
    _, _, source, job, link = _seed_currentness(db_session, suffix="stale-input")
    link.analysis_input_version = "analysis:stale-input:old"
    db_session.flush()

    authorization = W3AuthorityService(db_session).current(job_id=job.id, source_id=source.id)

    assert authorization.context.analysis_input_version == job.analysis_input_version
    assert authorization.active is False


@pytest.mark.postgres
def test_authority_lookup_selects_only_currentness_columns(
    db_session: Session,
    migrated_engine: Engine,
) -> None:
    _, _, source, job, _ = _seed_currentness(db_session, suffix="minimal")
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.lower())

    event.listen(migrated_engine, "before_cursor_execute", capture)
    try:
        W3AuthorityService(db_session).current(job_id=job.id, source_id=source.id)
    finally:
        event.remove(migrated_engine, "before_cursor_execute", capture)

    authority_select = next(
        statement for statement in statements if "job_source_links" in statement
    )
    assert "users.email" not in authority_select
    assert "users.display_name" not in authority_select
    assert "sources.canonical_url" not in authority_select
    assert "sources.metadata" not in authority_select
