from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.persistence import (
    Base,
    CollectionAttempt,
    Company,
    Evidence,
    ExtractionRevision,
    OutboxEvent,
    ParserExecution,
    RequestDeduplication,
    Source,
    SourcePolicyDecision,
    SourceVersion,
    StaleExecution,
    assert_current_attempt,
)

pytestmark = pytest.mark.approved_postgres

NOW = datetime(2026, 9, 9, tzinfo=UTC)
OWNER_A = UUID("00000000-0000-4000-8000-000000000101")
OWNER_B = UUID("00000000-0000-4000-8000-000000000102")


@pytest.fixture
def database_engine(approved_postgres_url) -> Engine:
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_w2_test_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = admin_engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(database_engine, expire_on_commit=False)


def _seed_source(session: Session) -> tuple[Company, Source, SourcePolicyDecision]:
    company = Company(
        company_id=uuid4(),
        legal_name="Synthetic Example Korea Ltd.",
        aliases=["Synthetic Example"],
        official_domains=["example.test"],
        legal_identifiers={"synthetic_registration": "SYN-001"},
        identity_status="verified",
        identity_evidence=["synthetic://company-evidence"],
    )
    source = Source(
        source_id=uuid4(),
        company_id=company.company_id,
        source_type="job_posting",
        canonical_url="https://jobs.example.test/opening/1",
        title="Synthetic Platform Engineer",
    )
    policy = SourcePolicyDecision(
        policy_decision_id=uuid4(),
        source_id=source.source_id,
        revision=1,
        official_status="verified",
        access_class="public",
        collection_permission="allowed",
        excerpt_storage_permission="allowed",
        body_storage_permission="denied",
        redistribution_permission="unknown",
        evidence_refs=["synthetic://policy-evidence"],
        checked_at=NOW,
        policy_version="synthetic-policy-v1",
    )
    session.add_all([company, source, policy])
    session.commit()
    return company, source, policy


def _version(
    source: Source,
    policy: SourcePolicyDecision,
    *,
    version_id: UUID | None = None,
    content_hash: str = "a" * 64,
) -> SourceVersion:
    return SourceVersion(
        source_version_id=version_id or uuid4(),
        source_id=source.source_id,
        company_id=source.company_id,
        title=source.title,
        source_type=source.source_type,
        canonical_url=source.canonical_url,
        content_hash=content_hash,
        hash_profile_version="html-v1",
        representation="html",
        first_parser_version="job-posting-v1",
        collected_at=NOW,
        published_at={"status": "unknown", "raw_text": None, "value": None},
        valid_from={"status": "unknown", "raw_text": None, "value": None},
        valid_to={"status": "unknown", "raw_text": None, "value": None},
        language="ko",
        policy_decision_id=policy.policy_decision_id,
    )


def _evidence(version_id: UUID, *, evidence_id: UUID | None = None) -> Evidence:
    return Evidence(
        evidence_id=evidence_id or uuid4(),
        source_version_id=version_id,
        evidence_key="required:0",
        section_title="필수요건",
        text_excerpt="합성 AWS 운영 경험",
        locator={"kind": "css", "selector": "#requirements", "start": 0, "end": 12},
        chunk_order=0,
        origin_kind="direct",
    )


@pytest.mark.parametrize(
    ("status", "output_hash", "include_revision", "valid"),
    [
        ("succeeded", "b" * 64, True, True),
        ("failed", None, False, True),
        ("succeeded", None, False, False),
        ("failed", "b" * 64, False, False),
        ("failed", "b" * 64, True, False),
    ],
)
def test_parser_execution_status_controls_output_and_revision(
    session_factory: sessionmaker[Session],
    status: str,
    output_hash: str | None,
    include_revision: bool,
    valid: bool,
) -> None:
    with session_factory() as session:
        _, source, policy = _seed_source(session)
        version = _version(source, policy)
        session.add(version)
        session.commit()

        revision = ExtractionRevision(
            extraction_revision_id=uuid4(),
            source_version_id=version.source_version_id,
            parser_version="job-posting-v1",
            output_hash="b" * 64,
            created_at=NOW,
            extraction_status="complete",
            posting_sections=[],
            date_values=[],
            limitations=[],
        )
        if include_revision:
            session.add(revision)
            session.flush()

        execution = ParserExecution(
            parser_execution_id=uuid4(),
            source_id=source.source_id,
            source_version_id=version.source_version_id,
            content_hash=version.content_hash,
            parser_version="job-posting-v1",
            output_hash=output_hash,
            extraction_revision_id=(revision.extraction_revision_id if include_revision else None),
            status=status,
            executed_at=NOW,
        )
        session.add(execution)

        if valid:
            session.commit()
            assert session.get(ParserExecution, execution.parser_execution_id) is execution
        else:
            with pytest.raises(IntegrityError):
                session.commit()


def test_source_unique_key_and_evidence_foreign_key_are_enforced(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        company, source, _policy = _seed_source(session)
        session.add(
            Source(
                source_id=uuid4(),
                company_id=company.company_id,
                source_type="job_posting",
                canonical_url=source.canonical_url,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(_evidence(uuid4()))
        with pytest.raises(IntegrityError):
            session.commit()


def test_concurrent_same_content_commit_creates_one_source_version(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _company, source, policy = _seed_source(session)
        source_id = source.source_id
        policy_id = policy.policy_decision_id

    barrier = Barrier(2)

    def insert_once() -> str:
        with session_factory() as session:
            source = session.get(Source, source_id)
            policy = session.get(SourcePolicyDecision, policy_id)
            assert source is not None and policy is not None
            session.add(_version(source, policy))
            barrier.wait()
            try:
                session.commit()
                return "inserted"
            except IntegrityError:
                session.rollback()
                return "deduplicated"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _index: insert_once(), range(2)))

    assert outcomes == ["deduplicated", "inserted"]
    with session_factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(SourceVersion)
            .where(SourceVersion.source_id == source_id)
        )
    assert count == 1


def test_version_evidence_and_outbox_rollback_as_one_transaction(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _company, source, policy = _seed_source(session)
        version = _version(source, policy)
        session.add_all(
            [
                version,
                _evidence(version.source_version_id),
                OutboxEvent(
                    event_id=uuid4(),
                    aggregate_id=source.source_id,
                    aggregate_revision=0,
                    event_type="source.version.available",
                    schema_version="w2.source.v1",
                    payload={"synthetic": True},
                    occurred_at=NOW,
                    delivery_state="pending",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 0
        assert session.scalar(select(func.count()).select_from(Evidence)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_public_tables_do_not_contain_private_job_or_owner_columns() -> None:
    forbidden = {"owner_user_id", "authenticated_owner_ref", "job_id", "project_id"}
    for model in (Company, Source, SourceVersion, Evidence, OutboxEvent):
        assert forbidden.isdisjoint(model.__table__.columns.keys())

    private_columns = set(CollectionAttempt.__table__.columns.keys())
    assert {"owner_user_id", "job_id", "project_id", "execution_fence"} <= private_columns


def test_stale_owner_deletion_epoch_and_execution_fence_are_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        _company, source, _policy = _seed_source(session)
        attempt = CollectionAttempt(
            attempt_id=uuid4(),
            owner_user_id=OWNER_A,
            job_id=uuid4(),
            project_id=None,
            command_id=uuid4(),
            input_version=1,
            target_ref=str(source.source_id),
            purpose_ref="synthetic-purpose",
            core_source_decision={
                "is_core": False,
                "decided_by": "synthetic-test",
                "rationale": "standalone collection",
                "decision_revision": 1,
                "analysis_input_version": 1,
            },
            resume_stage="policy",
            policy_revision=None,
            result_version=1,
            execution_fence="fence-2",
            owner_deletion_epoch=2,
            parser_execution_id=None,
            checkpoint_ref=None,
            result_refs=[],
            failures=[],
            required_actions=[],
        )
        session.add(attempt)
        session.commit()

        assert_current_attempt(
            session,
            attempt_id=attempt.attempt_id,
            owner_user_id=OWNER_A,
            execution_fence="fence-2",
            owner_deletion_epoch=2,
        )
        with pytest.raises(StaleExecution):
            assert_current_attempt(
                session,
                attempt_id=attempt.attempt_id,
                owner_user_id=OWNER_A,
                execution_fence="fence-1",
                owner_deletion_epoch=2,
            )
        with pytest.raises(StaleExecution):
            assert_current_attempt(
                session,
                attempt_id=attempt.attempt_id,
                owner_user_id=OWNER_A,
                execution_fence="fence-2",
                owner_deletion_epoch=1,
            )


def test_request_deduplication_is_scoped_by_owner_and_operation(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        common = {
            "idempotency_key": "same-button-click",
            "request_hash": "b" * 64,
            "accepted_resource_ref": "job:synthetic",
            "input_version": 1,
            "created_at": NOW,
        }
        session.add_all(
            [
                RequestDeduplication(
                    request_deduplication_id=uuid4(),
                    owner_user_id=OWNER_A,
                    operation="POST:/api/v1/sources/1/refresh",
                    **common,
                ),
                RequestDeduplication(
                    request_deduplication_id=uuid4(),
                    owner_user_id=OWNER_B,
                    operation="POST:/api/v1/sources/1/refresh",
                    **common,
                ),
                RequestDeduplication(
                    request_deduplication_id=uuid4(),
                    owner_user_id=OWNER_A,
                    operation="POST:/api/v1/sources/1/retry",
                    **common,
                ),
            ]
        )
        session.commit()

        session.add(
            RequestDeduplication(
                request_deduplication_id=uuid4(),
                owner_user_id=OWNER_A,
                operation="POST:/api/v1/sources/1/refresh",
                **common,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
