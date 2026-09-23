from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.application_workspace import Company
from app.models.sources import Source
from app.repo.source_collections import SourceCollectionRepository
from app.services.source_url_policy import canonicalize_official_url


@pytest.mark.postgres
def test_resolve_or_create_reuses_canonical_company_url(db_session: Session) -> None:
    company = Company(
        legal_name="Canonical Corp",
        display_name="Canonical Corp",
        official_domain="careers.canonical.example",
        identification_status="VERIFIED",
    )
    db_session.add(company)
    db_session.flush()
    first_url = canonicalize_official_url(
        "HTTPS://CAREERS.CANONICAL.EXAMPLE:443/jobs/42",
        official_domain=company.official_domain,
    )
    second_url = canonicalize_official_url(
        "https://careers.canonical.example/jobs/42",
        official_domain=company.official_domain,
    )
    repository = SourceCollectionRepository(db_session)

    first, first_created = repository.resolve_or_create(
        company_id=company.id,
        source_type="JOB_POSTING",
        canonical=first_url,
        checked_at=datetime.now(UTC),
    )
    second, second_created = repository.resolve_or_create(
        company_id=company.id,
        source_type="JOB_POSTING",
        canonical=second_url,
        checked_at=datetime.now(UTC),
    )

    assert first.id == second.id
    assert first_created is True
    assert second_created is False
    assert len(db_session.scalars(select(Source).where(Source.company_id == company.id)).all()) == 1


@pytest.mark.postgres
def test_concurrent_resolve_or_create_converges_on_one_source(migrated_engine: Engine) -> None:
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    with factory.begin() as setup:
        company = Company(
            legal_name="Concurrent Corp",
            display_name="Concurrent Corp",
            official_domain="concurrent.example",
            identification_status="VERIFIED",
        )
        setup.add(company)
        setup.flush()
        company_id = company.id
    canonical = canonicalize_official_url(
        "https://concurrent.example/jobs/42", official_domain="concurrent.example"
    )
    barrier = Barrier(2)

    def resolve() -> tuple[object, bool]:
        with factory.begin() as session:
            barrier.wait(timeout=5)
            source, created = SourceCollectionRepository(session).resolve_or_create(
                company_id=company_id,
                source_type="JOB_POSTING",
                canonical=canonical,
                checked_at=datetime.now(UTC),
            )
            return source.id, created

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: resolve(), range(2)))

    assert results[0][0] == results[1][0]
    assert sorted(created for _source_id, created in results) == [False, True]
    with factory.begin() as verification:
        assert len(
            verification.scalars(
                select(Source).where(Source.company_id == company_id)
            ).all()
        ) == 1
