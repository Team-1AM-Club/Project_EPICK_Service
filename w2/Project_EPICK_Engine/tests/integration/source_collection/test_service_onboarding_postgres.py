"""Service-copy W1 Source onboarding against an isolated real PostgreSQL schema."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionStage,
    CoreSourceDecision,
    SourceType,
)
from epick_engine.source_collection.persistence import Base, Company, Source, SourcePolicyDecision
from epick_engine.source_collection.source_runtime_input import (
    SourceRuntimeInputError,
    SqlAlchemyCollectionInputProvider,
    parse_runtime_source_config_json,
)
from epick_engine.source_collection.w1_lookup_client import (
    SourceOnboardingLookupResponse,
    SourceOnboardingMetadata,
)

pytestmark = pytest.mark.approved_postgres


@pytest.fixture
def session_factory(approved_postgres_url) -> Iterator[sessionmaker[Session]]:
    admin = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_service_onboarding_{uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine: Engine = admin.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(engine, autoflush=False, expire_on_commit=False)
    finally:
        Base.metadata.drop_all(engine)
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _command(company_id: UUID, source_id: UUID) -> CollectionCommand:
    return CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=uuid4(),
        job_id=uuid4(),
        authenticated_owner_ref=uuid4(),
        project_ref=None,
        company_id=company_id,
        source_id=source_id,
        input_version=1,
        execution_fence="1",
        purpose_ref=uuid4(),
        core_source_decision=CoreSourceDecision(
            is_core=True,
            decided_by="W3",
            rationale="CORE_REQUIRED",
            decision_revision=1,
            analysis_input_version=1,
        ),
        resume_stage=CollectionStage.POLICY,
        policy_revision=None,
        owner_deletion_epoch=0,
    )


def _config(company_id: UUID):
    source_config = {
        "policy_revision": 1,
        "robots_permission": "allowed",
        "result_version": 1,
        "language": "ko",
        "redirect_robots_permissions": [],
        "limits": {
            "site_concurrency": 1,
            "global_concurrency": 2,
            "source_ttl_seconds": 300,
            "max_response_bytes": 1_048_576,
            "max_decompressed_bytes": 2_097_152,
            "connect_timeout_seconds": 3.0,
            "read_timeout_seconds": 5.0,
            "max_redirects": 2,
            "general_retry_limit": 0,
            "retention_days": 7,
        },
    }
    return parse_runtime_source_config_json(
        json.dumps(
            {
                "schema_version": "w2.source-runtime-config.v1",
                "claim_lease_seconds": 120,
                "sources": {},
                "approved_sites": [
                    {
                        "company_id": str(company_id),
                        "company_legal_name": "Synthetic Company",
                        "company_official_domain": "example.test",
                        "hostname": "careers.example.test",
                        "path_prefix": "/jobs/",
                        "w1_source_type": "JOB_POSTING",
                        "w2_source_type": "job_posting",
                        "company_identity_evidence": ["synthetic:company"],
                        "policy": {
                            "official_status": "verified",
                            "access_class": "public",
                            "collection_permission": "allowed",
                            "excerpt_storage_permission": "allowed",
                            "body_storage_permission": "denied",
                            "redistribution_permission": "denied",
                            "evidence_refs": ["synthetic:policy"],
                            "checked_at": datetime.now(UTC).isoformat(),
                            "policy_version": "synthetic-v1",
                        },
                        "source_config": source_config,
                    }
                ],
            }
        )
    )


class _W1MetadataClient:
    def __init__(self, company_id: UUID, source_id: UUID, *, url: str) -> None:
        self.company_id = company_id
        self.source_id = source_id
        self.url = url
        self.calls = 0

    def lookup_source_onboarding(self, request):
        self.calls += 1
        return SourceOnboardingLookupResponse(
            schema_version="w1.private.source-onboarding-lookup.v1",
            command_id=request.command_id,
            status="AVAILABLE",
            source=SourceOnboardingMetadata(
                source_id=self.source_id,
                company_id=self.company_id,
                canonical_url=self.url,
                source_type="JOB_POSTING",
                title="Synthetic job",
                company_legal_name="Synthetic Company",
                company_official_domain="example.test",
                company_identification_status="VERIFIED",
            ),
        )


def test_new_w1_source_onboards_exact_ids_without_manual_w2_sql(session_factory) -> None:
    company_id, source_id = uuid4(), uuid4()
    command = _command(company_id, source_id)
    config = _config(company_id)
    assert source_id not in config.sources
    with pytest.raises(SourceRuntimeInputError, match="approved source config"):
        SqlAlchemyCollectionInputProvider(session_factory, config).load(command)
    with session_factory() as session:
        assert session.get(Source, source_id) is None

    client = _W1MetadataClient(
        company_id, source_id, url="https://careers.example.test/jobs/123"
    )
    provider = SqlAlchemyCollectionInputProvider(
        session_factory, config, onboarding_lookup_client=client
    )
    first = provider.load(command)
    second = provider.load(command)
    assert first.source_id == second.source_id == source_id
    assert first.company_id == second.company_id == company_id
    assert first.source_type is SourceType.JOB_POSTING
    assert first.policy_revision == 1
    assert client.calls == 2
    with session_factory() as session:
        assert session.get(Company, company_id) is not None
        assert session.get(Source, source_id).canonical_url == first.source_url
        assert session.scalar(
            select(func.count()).select_from(SourcePolicyDecision).where(
                SourcePolicyDecision.source_id == source_id
            )
        ) == 1


def test_unapproved_url_does_not_create_w2_rows(session_factory) -> None:
    company_id, source_id = uuid4(), uuid4()
    client = _W1MetadataClient(
        company_id, source_id, url="https://careers.example.test/jobs-other/123"
    )
    provider = SqlAlchemyCollectionInputProvider(
        session_factory, _config(company_id), onboarding_lookup_client=client
    )
    with pytest.raises(SourceRuntimeInputError, match="site approval"):
        provider.load(_command(company_id, source_id))
    with session_factory() as session:
        assert session.get(Company, company_id) is None
        assert session.get(Source, source_id) is None
