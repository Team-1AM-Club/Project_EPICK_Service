from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from tests.integration.source_collection.test_atomic_persistence import (
    _command as _atomic_command,
)
from tests.integration.source_collection.test_atomic_persistence import (
    _complete_prepared as _atomic_complete_prepared,
)
from tests.integration.source_collection.test_atomic_persistence import (
    _locker as _atomic_locker,
)
from tests.integration.source_collection.test_atomic_persistence import (
    _seed_source as _atomic_seed_source,
)
from tests.integration.source_collection.test_atomic_persistence import (
    commit_prepared_collection,
)

import epick_engine.source_collection.persistence as persistence_module
from epick_engine.source_collection.contracts import (
    ExtractionStatus,
    PostingSectionKind,
    SourceType,
)
from epick_engine.source_collection.contracts import (
    PostingSection as PostingSectionValue,
)
from epick_engine.source_collection.persistence import (
    Base,
    CollectionAttempt,
    Company,
    Evidence,
    ExtractionRevision,
    ExtractionRevisionEvidence,
    InvalidPreparedCollection,
    JobPosting,
    OutboxEvent,
    ParserExecution,
    PersistenceConflict,
    PostingSection,
    PostingSectionEvidence,
    PreparedExtractionRevision,
    Source,
    SourceObservation,
    SourcePolicyDecision,
    SourceVersion,
    resolve_job_posting,
)

pytestmark = pytest.mark.approved_postgres

PROJECT_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


@pytest.fixture
def database_engine(approved_postgres_url: URL) -> Iterator[Engine]:
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_job_posting_{uuid4().hex}"
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


def _company(*, company_id: UUID | None = None) -> Company:
    return Company(
        company_id=company_id or uuid4(),
        legal_name="Synthetic Job Posting Company",
        aliases=[],
        official_domains=["jobs.example.test"],
        legal_identifiers={},
        identity_status="verified",
        identity_evidence=["synthetic://company"],
    )


def _source(company: Company, *, source_type: str = "job_posting") -> Source:
    return Source(
        source_id=uuid4(),
        company_id=company.company_id,
        source_type=source_type,
        canonical_url=f"https://jobs.example.test/{uuid4()}",
        title="Platform Engineer",
    )


def test_resolve_job_posting_reuses_one_row_sequentially(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        company = _company()
        source = _source(company)
        session.add_all([company, source])

    first_candidate = uuid4()
    with session_factory.begin() as session:
        first = resolve_job_posting(
            session,
            candidate_job_posting_id=first_candidate,
            source_id=source.source_id,
            company_id=company.company_id,
        )
    with session_factory.begin() as session:
        replay = resolve_job_posting(
            session,
            candidate_job_posting_id=uuid4(),
            source_id=source.source_id,
            company_id=company.company_id,
        )

    assert first.job_posting_id == replay.job_posting_id == first_candidate
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JobPosting)) == 1


def test_resolve_job_posting_is_concurrency_safe(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        company = _company()
        source = _source(company)
        session.add_all([company, source])

    barrier = Barrier(2)

    def resolve() -> UUID:
        with session_factory.begin() as session:
            barrier.wait(timeout=10)
            return resolve_job_posting(
                session,
                candidate_job_posting_id=uuid4(),
                source_id=source.source_id,
                company_id=company.company_id,
            ).job_posting_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(resolve) for _ in range(2)]
        resolved_ids = [future.result(timeout=15) for future in futures]

    assert resolved_ids[0] == resolved_ids[1]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(JobPosting)) == 1


def test_resolve_job_posting_rejects_wrong_company_and_non_job_source(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        company = _company()
        other_company = _company()
        job_source = _source(company)
        non_job_source = _source(company, source_type="company_site")
        session.add_all([company, other_company, job_source, non_job_source])

    with session_factory() as session:
        with pytest.raises(ValueError, match="requested company"):
            resolve_job_posting(
                session,
                candidate_job_posting_id=uuid4(),
                source_id=job_source.source_id,
                company_id=other_company.company_id,
            )
        session.rollback()

    with session_factory() as session:
        with pytest.raises(ValueError, match="not a job posting"):
            resolve_job_posting(
                session,
                candidate_job_posting_id=uuid4(),
                source_id=non_job_source.source_id,
                company_id=company.company_id,
            )
        session.rollback()


def _commit_state_counts(session: Session) -> tuple[int, ...]:
    models = (
        SourceVersion,
        Evidence,
        ExtractionRevision,
        ExtractionRevisionEvidence,
        PostingSection,
        PostingSectionEvidence,
        ParserExecution,
        SourceObservation,
        CollectionAttempt,
        OutboxEvent,
    )
    return tuple(
        int(session.scalar(select(func.count()).select_from(model)) or 0) for model in models
    )


@pytest.mark.parametrize("link_change", ["add", "omit"])
def test_commit_rejects_changed_existing_revision_evidence_links_and_rolls_back(
    session_factory: sessionmaker[Session],
    link_change: str,
) -> None:
    _company_row, source, policy = _atomic_seed_source(session_factory)
    first_command = _atomic_command(source)
    first = _atomic_complete_prepared(
        first_command,
        source,
        policy,
        aggregate_revision=1,
    )
    if link_change == "omit":
        extra = replace(
            first.evidence[0],
            evidence_id=uuid4(),
            evidence_key="unreferenced:0",
            chunk_order=1,
        )
        first = replace(
            first,
            evidence=(*first.evidence, extra),
            extraction_revision=replace(
                first.extraction_revision,
                evidence_ids=(*first.extraction_revision.evidence_ids, extra.evidence_id),
            ),
        )
    commit_prepared_collection(
        session_factory,
        command=first_command,
        prepared=first,
        lock_authority=_atomic_locker(first_command, pointer_eligible=True),
    )

    with session_factory() as session:
        before = _commit_state_counts(session)

    replay_command = _atomic_command(source)
    replay = _atomic_complete_prepared(
        replay_command,
        source,
        policy,
        aggregate_revision=2,
    )
    if link_change == "add":
        extra = replace(
            replay.evidence[0],
            evidence_id=uuid4(),
            evidence_key="unreferenced:0",
            chunk_order=1,
        )
        replay = replace(
            replay,
            evidence=(*replay.evidence, extra),
            extraction_revision=replace(
                replay.extraction_revision,
                evidence_ids=(*replay.extraction_revision.evidence_ids, extra.evidence_id),
            ),
        )

    with pytest.raises(PersistenceConflict, match="extraction evidence links"):
        commit_prepared_collection(
            session_factory,
            command=replay_command,
            prepared=replay,
            lock_authority=_atomic_locker(replay_command, pointer_eligible=True),
        )

    with session_factory() as session:
        assert _commit_state_counts(session) == before


def test_commit_rejects_sections_for_non_job_source_version(
    session_factory: sessionmaker[Session],
) -> None:
    _company_row, source, policy = _atomic_seed_source(session_factory)
    command_value = _atomic_command(source)
    prepared = _atomic_complete_prepared(
        command_value,
        source,
        policy,
        aggregate_revision=1,
    )
    prepared = replace(
        prepared,
        source_version=replace(
            prepared.source_version,
            source_type=SourceType.COMPANY_WEBSITE,
        ),
    )

    with pytest.raises(InvalidPreparedCollection, match="job_posting source version"):
        commit_prepared_collection(
            session_factory,
            command=command_value,
            prepared=prepared,
            lock_authority=_atomic_locker(command_value, pointer_eligible=True),
        )

    with session_factory() as session:
        assert _commit_state_counts(session) == (0,) * 10


def test_section_canonicalization_replaces_only_evidence_ids() -> None:
    candidate_id = uuid4()
    canonical_evidence_id = uuid4()
    raw_value = str(candidate_id)
    section = PostingSectionValue(
        section_key=raw_value,
        kind="general",
        heading_raw=raw_value,
        text_raw=raw_value,
        evidence_ids=[candidate_id],
        order=0,
        relation_text=raw_value,
    )

    canonical = persistence_module._replace_posting_section_evidence_ids(
        section,
        {candidate_id: canonical_evidence_id},
    )

    assert canonical.evidence_ids == [canonical_evidence_id]
    assert canonical.section_key == raw_value
    assert canonical.heading_raw == raw_value
    assert canonical.text_raw == raw_value
    assert canonical.relation_text == raw_value


def _seed_normalized_revision(
    session: Session,
) -> tuple[PreparedExtractionRevision, SourceVersion, tuple[Evidence, Evidence]]:
    company = _company()
    source = _source(company)
    policy = SourcePolicyDecision(
        policy_decision_id=uuid4(),
        source_id=source.source_id,
        revision=1,
        official_status="verified",
        access_class="public",
        collection_permission="allowed",
        excerpt_storage_permission="allowed",
        body_storage_permission="allowed",
        redistribution_permission="allowed",
        evidence_refs=["synthetic://policy"],
        checked_at=NOW,
        policy_version="policy-v1",
    )
    version = SourceVersion(
        source_version_id=uuid4(),
        source_id=source.source_id,
        company_id=company.company_id,
        title=source.title,
        source_type=source.source_type,
        canonical_url=source.canonical_url,
        content_hash="a" * 64,
        hash_profile_version="html-v1",
        representation="static_html",
        first_parser_version="parser-v1",
        collected_at=NOW,
        published_at={
            "status": "unknown",
            "raw_text": None,
            "value": None,
            "precision": None,
            "timezone": None,
        },
        valid_from={
            "status": "unknown",
            "raw_text": None,
            "value": None,
            "precision": None,
            "timezone": None,
        },
        valid_to={
            "status": "unknown",
            "raw_text": None,
            "value": None,
            "precision": None,
            "timezone": None,
        },
        language="en",
        policy_decision_id=policy.policy_decision_id,
    )
    evidence = (
        Evidence(
            evidence_id=uuid4(),
            source_version_id=version.source_version_id,
            evidence_key="required:0",
            section_title="Required",
            text_excerpt="Python",
            locator={
                "kind": "css",
                "value": "#required",
                "normalization_version": None,
                "start": None,
                "end": None,
            },
            chunk_order=0,
            origin_kind="direct",
        ),
        Evidence(
            evidence_id=uuid4(),
            source_version_id=version.source_version_id,
            evidence_key="required:1",
            section_title="Required",
            text_excerpt="PostgreSQL",
            locator={
                "kind": "css",
                "value": "#required",
                "normalization_version": None,
                "start": None,
                "end": None,
            },
            chunk_order=1,
            origin_kind="direct",
        ),
    )
    section = PostingSectionValue(
        section_key="required",
        kind="required",
        heading_raw="Required",
        text_raw="Python and PostgreSQL",
        evidence_ids=[evidence[1].evidence_id, evidence[0].evidence_id],
        order=0,
        relation_text=None,
    )
    revision = PreparedExtractionRevision(
        extraction_revision_id=uuid4(),
        source_version_id=version.source_version_id,
        parser_version="parser-v1",
        output_hash="b" * 64,
        created_at=NOW,
        extraction_status=ExtractionStatus.COMPLETE,
        posting_sections=(section,),
        date_values={},
        limitations=(),
        evidence_ids=(evidence[0].evidence_id, evidence[1].evidence_id),
    )
    session.add(company)
    session.flush()
    session.add(source)
    session.flush()
    session.add(policy)
    session.flush()
    session.add(version)
    session.flush()
    session.add_all(evidence)
    session.flush()
    session.add(
        ExtractionRevision(
            extraction_revision_id=revision.extraction_revision_id,
            source_version_id=revision.source_version_id,
            parser_version=revision.parser_version,
            output_hash=revision.output_hash,
            created_at=revision.created_at,
            extraction_status=revision.extraction_status.value,
            posting_sections=[section.model_dump(mode="json")],
            date_values={},
            limitations=[],
        )
    )
    session.flush()
    session.add_all(
        [
            ExtractionRevisionEvidence(
                extraction_revision_id=revision.extraction_revision_id,
                evidence_id=item.evidence_id,
                source_version_id=version.source_version_id,
            )
            for item in evidence
        ]
    )
    session.flush()
    return revision, version, evidence


def test_normalized_sections_are_immutable_and_preserve_evidence_order(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        revision, version, evidence = _seed_normalized_revision(session)
        persistence_module._persist_posting_sections(
            session,
            revision=revision,
            revision_was_created=True,
        )

    with session_factory.begin() as session:
        persistence_module._persist_posting_sections(
            session,
            revision=revision,
            revision_was_created=False,
        )
        assert session.scalar(select(func.count()).select_from(PostingSection)) == 1
        assert session.scalar(select(func.count()).select_from(PostingSectionEvidence)) == 2

    changed_section = revision.posting_sections[0].model_copy(update={"text_raw": "Changed"})
    changed_revision = replace(revision, posting_sections=(changed_section,))
    with session_factory() as session:
        with pytest.raises(PersistenceConflict, match="immutable payload"):
            persistence_module._persist_posting_sections(
                session,
                revision=changed_revision,
                revision_was_created=False,
            )
        session.rollback()

    added_section = PostingSectionValue(
        section_key="preferred",
        kind="preferred",
        heading_raw="Preferred",
        text_raw="Kubernetes",
        evidence_ids=[evidence[0].evidence_id],
        order=1,
        relation_text=None,
    )
    changed_section_sets = (
        replace(revision, posting_sections=()),
        replace(revision, posting_sections=(*revision.posting_sections, added_section)),
        replace(
            revision,
            posting_sections=(
                revision.posting_sections[0].model_copy(
                    update={
                        "evidence_ids": list(reversed(revision.posting_sections[0].evidence_ids))
                    }
                ),
            ),
        ),
    )
    for changed_section_set in changed_section_sets:
        with session_factory() as session:
            with pytest.raises(PersistenceConflict):
                persistence_module._persist_posting_sections(
                    session,
                    revision=changed_section_set,
                    revision_was_created=False,
                )
            session.rollback()

    with session_factory.begin() as session:
        original_before = tuple(
            session.execute(
                select(
                    PostingSection.section_key,
                    PostingSection.text_raw,
                    PostingSectionEvidence.evidence_id,
                    PostingSectionEvidence.evidence_order,
                )
                .join(
                    PostingSectionEvidence,
                    (
                        PostingSectionEvidence.extraction_revision_id
                        == PostingSection.extraction_revision_id
                    )
                    & (PostingSectionEvidence.section_key == PostingSection.section_key),
                )
                .where(PostingSection.extraction_revision_id == revision.extraction_revision_id)
                .order_by(PostingSectionEvidence.evidence_order)
            )
        )
        assert [row.evidence_id for row in original_before] == [
            evidence[1].evidence_id,
            evidence[0].evidence_id,
        ]

        new_section = revision.posting_sections[0].model_copy(
            update={
                "section_key": "preferred",
                "kind": PostingSectionKind.PREFERRED,
                "text_raw": "Kubernetes",
            }
        )
        new_revision = replace(
            revision,
            extraction_revision_id=uuid4(),
            output_hash="c" * 64,
            posting_sections=(new_section,),
        )
        session.add(
            ExtractionRevision(
                extraction_revision_id=new_revision.extraction_revision_id,
                source_version_id=version.source_version_id,
                parser_version=new_revision.parser_version,
                output_hash=new_revision.output_hash,
                created_at=new_revision.created_at,
                extraction_status=new_revision.extraction_status.value,
                posting_sections=[new_section.model_dump(mode="json")],
                date_values={},
                limitations=[],
            )
        )
        session.flush()
        session.add_all(
            [
                ExtractionRevisionEvidence(
                    extraction_revision_id=new_revision.extraction_revision_id,
                    evidence_id=item.evidence_id,
                    source_version_id=version.source_version_id,
                )
                for item in evidence
            ]
        )
        session.flush()
        persistence_module._persist_posting_sections(
            session,
            revision=new_revision,
            revision_was_created=True,
        )

    with session_factory() as session:
        original_after = tuple(
            session.execute(
                select(
                    PostingSection.section_key,
                    PostingSection.text_raw,
                    PostingSectionEvidence.evidence_id,
                    PostingSectionEvidence.evidence_order,
                )
                .join(
                    PostingSectionEvidence,
                    (
                        PostingSectionEvidence.extraction_revision_id
                        == PostingSection.extraction_revision_id
                    )
                    & (PostingSectionEvidence.section_key == PostingSection.section_key),
                )
                .where(PostingSection.extraction_revision_id == revision.extraction_revision_id)
                .order_by(PostingSectionEvidence.evidence_order)
            )
        )
        assert original_after == original_before
        new_section_row = session.scalar(
            select(PostingSection).where(
                PostingSection.extraction_revision_id == new_revision.extraction_revision_id
            )
        )
        assert new_section_row is not None
        assert (
            new_section_row.section_key,
            new_section_row.kind,
            new_section_row.order,
        ) == ("preferred", "preferred", 0)
        new_evidence_rows = tuple(
            session.execute(
                select(
                    PostingSectionEvidence.evidence_id,
                    PostingSectionEvidence.evidence_order,
                )
                .where(
                    PostingSectionEvidence.extraction_revision_id
                    == new_revision.extraction_revision_id
                )
                .order_by(PostingSectionEvidence.evidence_order)
            )
        )
        assert new_evidence_rows == (
            (evidence[1].evidence_id, 0),
            (evidence[0].evidence_id, 1),
        )


@contextmanager
def _legacy_schema(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, Config]]:
    schema_name = f"epick_job_migration_{uuid4().hex}"
    schema_url = approved_postgres_url.update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema_engine = create_engine(schema_url, pool_pre_ping=True)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    monkeypatch.setenv("EPICK_DATABASE_URL", schema_url.render_as_string(hide_password=False))
    try:
        yield schema_engine, config
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def _seed_legacy_revision(
    engine: Engine,
    *,
    posting_sections: object,
    evidence_ids: tuple[UUID, ...],
    linked_evidence_ids: tuple[UUID, ...] | None = None,
) -> tuple[UUID, UUID]:
    company_id = uuid4()
    source_id = uuid4()
    policy_id = uuid4()
    source_version_id = uuid4()
    extraction_revision_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO companies (
                    company_id, legal_name, aliases, official_domains,
                    legal_identifiers, identity_status, identity_evidence
                ) VALUES (
                    :company_id, 'Legacy Company', ARRAY[]::text[],
                    ARRAY['jobs.example.test']::text[], '{}'::jsonb,
                    'verified', ARRAY['synthetic://legacy']::text[]
                )
                """
            ),
            {"company_id": company_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO sources (
                    source_id, company_id, source_type, canonical_url, title
                ) VALUES (
                    :source_id, :company_id, 'job_posting',
                    'https://jobs.example.test/legacy', 'Legacy Role'
                )
                """
            ),
            {"source_id": source_id, "company_id": company_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO source_policy_decisions (
                    policy_decision_id, source_id, revision, official_status,
                    access_class, collection_permission, excerpt_storage_permission,
                    body_storage_permission, redistribution_permission, evidence_refs,
                    checked_at, policy_version
                ) VALUES (
                    :policy_id, :source_id, 1, 'verified', 'public', 'allowed',
                    'allowed', 'allowed', 'allowed', ARRAY['synthetic://policy']::text[],
                    :checked_at, 'policy-v1'
                )
                """
            ),
            {"policy_id": policy_id, "source_id": source_id, "checked_at": NOW},
        )
        connection.execute(
            text(
                """
                INSERT INTO source_versions (
                    source_version_id, source_id, company_id, title, source_type,
                    canonical_url, content_hash, hash_profile_version, representation,
                    first_parser_version, collected_at, published_at, valid_from,
                    valid_to, language, policy_decision_id
                ) VALUES (
                    :version_id, :source_id, :company_id, 'Legacy Role', 'job_posting',
                    'https://jobs.example.test/legacy', :content_hash, 'html-v1',
                    'static_html', 'parser-v1', :collected_at,
                    '{"status":"unknown"}'::jsonb, '{"status":"unknown"}'::jsonb,
                    '{"status":"unknown"}'::jsonb, 'en', :policy_id
                )
                """
            ),
            {
                "version_id": source_version_id,
                "source_id": source_id,
                "company_id": company_id,
                "content_hash": "d" * 64,
                "collected_at": NOW,
                "policy_id": policy_id,
            },
        )
        for order, evidence_id in enumerate(evidence_ids):
            connection.execute(
                text(
                    """
                    INSERT INTO evidence (
                        evidence_id, source_version_id, evidence_key, section_title,
                        text_excerpt, locator, chunk_order, origin_kind
                    ) VALUES (
                        :evidence_id, :version_id, :evidence_key, 'Required',
                        :text_excerpt, '{"kind":"css","value":"#required"}'::jsonb,
                        :chunk_order, 'direct'
                    )
                    """
                ),
                {
                    "evidence_id": evidence_id,
                    "version_id": source_version_id,
                    "evidence_key": f"legacy:{order}",
                    "text_excerpt": f"evidence-{order}",
                    "chunk_order": order,
                },
            )
        connection.execute(
            text(
                """
                INSERT INTO extraction_revisions (
                    extraction_revision_id, source_version_id, parser_version,
                    output_hash, created_at, extraction_status, posting_sections,
                    date_values, limitations
                ) VALUES (
                    :revision_id, :version_id, 'parser-v1', :output_hash, :created_at,
                    'complete', CAST(:posting_sections AS jsonb), '{}'::jsonb, '[]'::jsonb
                )
                """
            ),
            {
                "revision_id": extraction_revision_id,
                "version_id": source_version_id,
                "output_hash": "e" * 64,
                "created_at": NOW,
                "posting_sections": json.dumps(posting_sections),
            },
        )
        for evidence_id in linked_evidence_ids or evidence_ids:
            connection.execute(
                text(
                    """
                    INSERT INTO extraction_revision_evidence (
                        extraction_revision_id, evidence_id, source_version_id
                    ) VALUES (:revision_id, :evidence_id, :version_id)
                    """
                ),
                {
                    "revision_id": extraction_revision_id,
                    "evidence_id": evidence_id,
                    "version_id": source_version_id,
                },
            )
    return source_id, extraction_revision_id


def test_legacy_migration_backfills_exact_scalars_and_evidence_order(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_ids = (uuid4(), uuid4())
    posting_sections = [
        {
            "section_key": "required",
            "kind": "required",
            "heading_raw": "Required",
            "text_raw": "Python and PostgreSQL",
            "evidence_ids": [str(evidence_ids[1]), str(evidence_ids[0])],
            "order": 0,
            "relation_text": None,
        }
    ]
    with _legacy_schema(approved_postgres_url, monkeypatch) as (engine, config):
        command.upgrade(config, "0001_source_collection_core")
        source_id, revision_id = _seed_legacy_revision(
            engine,
            posting_sections=posting_sections,
            evidence_ids=evidence_ids,
        )

        command.upgrade(config, "head")

        with engine.begin() as connection:
            job_posting = connection.execute(
                text(
                    "SELECT job_posting_id, company_id, source_id "
                    "FROM job_postings WHERE source_id = :source_id"
                ),
                {"source_id": source_id},
            ).one()
            section = connection.execute(
                text(
                    "SELECT section_key, kind, heading_raw, text_raw, section_order, "
                    "relation_text FROM posting_sections "
                    "WHERE extraction_revision_id = :revision_id"
                ),
                {"revision_id": revision_id},
            ).one()
            ordered_evidence = (
                connection.execute(
                    text(
                        "SELECT evidence_id FROM posting_section_evidence "
                        "WHERE extraction_revision_id = :revision_id "
                        "ORDER BY evidence_order"
                    ),
                    {"revision_id": revision_id},
                )
                .scalars()
                .all()
            )
            legacy_json = connection.execute(
                text(
                    "SELECT posting_sections FROM extraction_revisions "
                    "WHERE extraction_revision_id = :revision_id"
                ),
                {"revision_id": revision_id},
            ).scalar_one()

        assert job_posting.job_posting_id == job_posting.source_id == source_id
        assert tuple(section) == (
            "required",
            "required",
            "Required",
            "Python and PostgreSQL",
            0,
            None,
        )
        assert ordered_evidence == [evidence_ids[1], evidence_ids[0]]
        assert legacy_json == posting_sections


@pytest.mark.parametrize("invalid_case", ["malformed", "duplicate", "unlinked"])
def test_legacy_migration_fails_closed_for_invalid_sections(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
    invalid_case: str,
) -> None:
    evidence_id = uuid4()
    section = {
        "section_key": "required",
        "kind": "required",
        "heading_raw": "Required",
        "text_raw": "Python",
        "evidence_ids": [str(evidence_id)],
        "order": 0,
        "relation_text": None,
    }
    posting_sections: object
    if invalid_case == "malformed":
        posting_sections = {"not": "an array"}
    elif invalid_case == "duplicate":
        posting_sections = [section, section]
    else:
        posting_sections = [{**section, "evidence_ids": [str(uuid4())]}]

    with _legacy_schema(approved_postgres_url, monkeypatch) as (engine, config):
        command.upgrade(config, "0001_source_collection_core")
        _seed_legacy_revision(
            engine,
            posting_sections=posting_sections,
            evidence_ids=(evidence_id,),
        )

        with pytest.raises(SQLAlchemyError):
            command.upgrade(config, "head")

        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == ("0001_source_collection_core")


def test_section_evidence_foreign_keys_reject_cross_revision_and_version(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        revision, version, _evidence = _seed_normalized_revision(session)
        persistence_module._persist_posting_sections(
            session,
            revision=revision,
            revision_was_created=True,
        )
        other_evidence = Evidence(
            evidence_id=uuid4(),
            source_version_id=version.source_version_id,
            evidence_key="other-revision",
            section_title=None,
            text_excerpt="Other revision",
            locator={"kind": "css", "value": "#other"},
            chunk_order=2,
            origin_kind="direct",
        )
        session.add(other_evidence)
        session.flush()
        other_revision_id = uuid4()
        session.add(
            ExtractionRevision(
                extraction_revision_id=other_revision_id,
                source_version_id=version.source_version_id,
                parser_version="parser-v1",
                output_hash="1" * 64,
                created_at=NOW,
                extraction_status="complete",
                posting_sections=[],
                date_values={},
                limitations=[],
            )
        )
        session.flush()
        session.add(
            ExtractionRevisionEvidence(
                extraction_revision_id=other_revision_id,
                evidence_id=other_evidence.evidence_id,
                source_version_id=version.source_version_id,
            )
        )

    with session_factory() as session:
        session.add(
            PostingSectionEvidence(
                extraction_revision_id=revision.extraction_revision_id,
                evidence_id=other_evidence.evidence_id,
                section_key="required",
                evidence_order=2,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    with session_factory.begin() as session:
        other_version = SourceVersion(
            source_version_id=uuid4(),
            source_id=version.source_id,
            company_id=version.company_id,
            title=version.title,
            source_type=version.source_type,
            canonical_url=version.canonical_url,
            content_hash="f" * 64,
            hash_profile_version=version.hash_profile_version,
            representation=version.representation,
            first_parser_version=version.first_parser_version,
            collected_at=NOW,
            published_at=version.published_at,
            valid_from=version.valid_from,
            valid_to=version.valid_to,
            language=version.language,
            policy_decision_id=version.policy_decision_id,
        )
        cross_version_evidence = Evidence(
            evidence_id=uuid4(),
            source_version_id=other_version.source_version_id,
            evidence_key="cross-version",
            section_title=None,
            text_excerpt="Cross version",
            locator={"kind": "css", "value": "#cross"},
            chunk_order=0,
            origin_kind="direct",
        )
        session.add(other_version)
        session.flush()
        session.add(cross_version_evidence)

    with session_factory() as session:
        session.add(
            ExtractionRevisionEvidence(
                extraction_revision_id=revision.extraction_revision_id,
                evidence_id=cross_version_evidence.evidence_id,
                source_version_id=version.source_version_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
