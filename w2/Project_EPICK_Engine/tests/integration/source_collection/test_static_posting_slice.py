"""T045 M1 static posting slice against approved PostgreSQL.

W1 job acceptance and the static transport are deliberately test-local.  The
collection worker, parser, PostgreSQL persistence, and public HTTP factories
remain production code.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import Message, Receive, Scope, Send

from epick_engine.source_collection.api import (
    ApiBoundarySettings,
    ApiErrorCode,
    ApiProblem,
    AuthenticatedPrincipal,
    CursorCodec,
    create_company_source_api,
    create_job_posting_api,
    create_version_evidence_api,
)
from epick_engine.source_collection.collector import (
    StaticFetchRequest,
    StaticFetchResult,
    StaticResponseCandidate,
)
from epick_engine.source_collection.contracts import (
    AccessClass,
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    CoreSourceDecision,
    ExtractionStatus,
    FreshnessStatus,
    OfficialStatus,
    Permission,
    Policy,
    PostingSectionKind,
    SourceEventType,
    SourceType,
)
from epick_engine.source_collection.contracts import (
    PostingSection as PostingSectionValue,
)
from epick_engine.source_collection.parsing import extract_static_candidate
from epick_engine.source_collection.persistence import (
    Base,
    Company,
    Evidence,
    ExecutionAuthorityGrant,
    ExtractionRevision,
    JobPosting,
    OutboxEvent,
    PostingSection,
    PostingSectionEvidence,
    RetainedBody,
    Source,
    SourcePolicyDecision,
    SourceVersion,
    resolve_job_posting,
)
from epick_engine.source_collection.persistence import (
    commit_prepared_collection as _commit_prepared_collection,
)
from epick_engine.source_collection.persistence import (
    replay_committed_collection as _replay_committed_collection,
)
from epick_engine.source_collection.policy import (
    ExecutionLimits,
    PolicyBlocked,
    PolicyOperation,
    Representation,
    UntrustedDocument,
    ValidatedTarget,
)
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
)
from epick_engine.source_collection.service import (
    EvidenceReadRecord,
    JobPostingContentRecord,
    JobPostingEvidenceSnapshot,
    JobPostingFailureReference,
    JobPostingImportAccepted,
    JobPostingRecord,
    JobPostingService,
    OwnedSourceRecord,
    OwnedSourceVersionRecord,
    SourceCurrentReadState,
    SourceRefreshAccepted,
    SourceVersionEvidenceService,
    SourceVersionReadRecord,
    StaticCollectionExecution,
    StaticCollectionInput,
)
from epick_engine.source_collection.worker import (
    SourceCollectionWorker,
    WorkerExecutionPermit,
)

pytestmark = pytest.mark.approved_postgres

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
OWNER_ID = UUID("00000000-0000-4000-8000-000000004501")
COMPANY_ID = UUID("00000000-0000-4000-8000-000000004502")
PROJECT_ID = UUID("00000000-0000-4000-8000-000000004503")


def _trusted_scope(command: CollectionCommand) -> PrivateWriteScope:
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=command.authenticated_owner_ref,
            owner_deletion_epoch=command.owner_deletion_epoch,
            scope=PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_ID),
            authority_ref="w1:test-static-slice-authority",
            command_id=command.command_id,
            job_id=command.job_id,
        )
    )


def commit_prepared_collection(session_factory, *, command, **kwargs):
    kwargs.setdefault("private_scope", _trusted_scope(command))
    return _commit_prepared_collection(session_factory, command=command, **kwargs)


def replay_committed_collection(session_factory, *, command, **kwargs):
    kwargs.setdefault("private_scope", _trusted_scope(command))
    return _replay_committed_collection(session_factory, command=command, **kwargs)


SOURCE_URL = "https://synthetic-meridian-careers.test/jobs/static-posting"
FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "synthetic_sources" / "static_posting.html"
)
HTML_A = FIXTURE_PATH.read_text(encoding="utf-8")
HTML_B = HTML_A.replace(
    "AWS experience is required.",
    "AWS experience across production systems is required.",
)
SETTINGS = ApiBoundarySettings(
    idempotency_key_max_length=256,
    cursor_max_token_length=1_024,
    page_default_limit=20,
    page_max_limit=100,
)


@dataclass(frozen=True)
class _AsgiResponse:
    status_code: int
    body: bytes

    def payload(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.body))


@pytest.fixture
def database_engine(approved_postgres_url: URL) -> Iterator[Engine]:
    """Give this slice a real, isolated PostgreSQL schema."""
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_static_posting_slice_{uuid4().hex}"
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


def _complete(awaitable: Awaitable[Any]) -> Any:
    return asyncio.run(awaitable)


def _request(
    app: FastAPI,
    method: str,
    target: str,
    *,
    headers: Mapping[str, str],
    body: Mapping[str, object] | None = None,
) -> _AsgiResponse:
    split = urlsplit(target)
    request_body = b"" if body is None else json.dumps(body).encode("utf-8")
    raw_headers = [(b"host", b"testserver")]
    raw_headers.extend(
        (name.lower().encode("ascii"), value.encode("utf-8")) for name, value in headers.items()
    )
    if body is not None:
        raw_headers.append((b"content-type", b"application/json"))
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": split.path,
            "raw_path": split.path.encode("ascii"),
            "query_string": split.query.encode("ascii"),
            "headers": raw_headers,
            "client": ("test-client", 1),
            "server": ("testserver", 80),
        },
    )
    sent = False
    messages: list[Message] = []

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": request_body, "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    _complete(app(scope, cast(Receive, receive), cast(Send, send)))
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        cast(bytes, message.get("body", b""))
        for message in messages
        if message["type"] == "http.response.body"
    )
    return _AsgiResponse(status_code=cast(int, start["status"]), body=response_body)


def _headers(idempotency_key: str | None = None) -> dict[str, str]:
    result = {"authorization": "Bearer static-slice-owner"}
    if idempotency_key is not None:
        result["Idempotency-Key"] = idempotency_key
    return result


def _limits() -> ExecutionLimits:
    return ExecutionLimits(
        site_concurrency=1,
        global_concurrency=1,
        source_ttl_seconds=60,
        max_response_bytes=1_000_000,
        max_decompressed_bytes=2_000_000,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_redirects=1,
        general_retry_limit=1,
        retention_days=1,
    )


def _policy(policy_decision_id: UUID) -> Policy:
    return Policy(
        policy_decision_id=policy_decision_id,
        official_status=OfficialStatus.VERIFIED,
        access_class=AccessClass.PUBLIC,
        collection_permission=Permission.ALLOWED,
        excerpt_storage_permission=Permission.ALLOWED,
        body_storage_permission=Permission.DENIED,
        redistribution_permission=Permission.ALLOWED,
        checked_at=NOW,
        policy_version="static-slice.integration.v1",
    )


@dataclass(frozen=True)
class _InputProvider:
    value: StaticCollectionInput

    def load(self, _command: CollectionCommand) -> StaticCollectionInput:
        return self.value


class _NoNetworkCollector:
    """Return the selected static fixture without opening a socket."""

    def __init__(self, document: str) -> None:
        self._document = document
        self.requests: list[StaticFetchRequest] = []

    def fetch(self, request: StaticFetchRequest) -> StaticFetchResult:
        self.requests.append(request)
        candidate = StaticResponseCandidate(
            final_target=ValidatedTarget(
                url=request.source_url,
                hostname="synthetic-meridian-careers.test",
                port=443,
                resolved_addresses=frozenset({"198.51.100.20"}),
            ),
            representation=Representation.HTML,
            document=UntrustedDocument(self._document),
            http_status=200,
            raw_size=len(self._document.encode("utf-8")),
            decompressed_size=len(self._document.encode("utf-8")),
        )
        return StaticFetchResult(
            command_id=request.command_id,
            candidate=candidate,
            failure_code=None,
        )

    def close(self) -> None:
        pass


class _W1Harness:
    def __init__(self, permit: WorkerExecutionPermit) -> None:
        self.permit = permit
        self.finalized: CollectionResult | None = None

    def authorize_execution(self, command: CollectionCommand) -> WorkerExecutionPermit:
        assert command == self.permit.command
        return self.permit

    def authorize_and_record_stage(
        self,
        permit: WorkerExecutionPermit,
        _stage: CollectionStage,
        *,
        policy_revision: int | None = None,
    ) -> WorkerExecutionPermit:
        if policy_revision is None:
            return permit
        self.permit = replace(
            permit,
            command=permit.command.model_copy(update={"policy_revision": policy_revision}),
        )
        return self.permit

    def finalize_execution(
        self,
        permit: WorkerExecutionPermit,
        result: CollectionResult,
        *,
        resources_closed: bool = True,
    ) -> None:
        assert resources_closed is True
        assert permit.command.command_id == result.command_id
        self.finalized = result

    def record_execution_stopped(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("the isolated W1 harness must finalize the worker")


class _StaticPostingSlice:
    """Test-only W1 acceptance around the production W2 collection boundary."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._source_id: UUID | None = None
        self._posting_id: UUID | None = None
        self._policy: Policy | None = None
        self._document = HTML_A
        self._source_owners: dict[UUID, UUID] = {}
        self._results: dict[UUID, CollectionResult] = {}
        self._posting_jobs: dict[UUID, UUID] = {}
        self._job_owners: dict[UUID, UUID] = {}
        self._refreshes: dict[str, SourceRefreshAccepted] = {}
        self.dispatches = 0
        with self._session_factory.begin() as session:
            session.add(
                Company(
                    company_id=COMPANY_ID,
                    legal_name="Synthetic Meridian Careers Ltd.",
                    aliases=["Synthetic Meridian Careers"],
                    official_domains=["synthetic-meridian-careers.test"],
                    legal_identifiers={},
                    identity_status="verified",
                    identity_evidence=["fixture:static_posting"],
                )
            )

    def resolve_company(
        self,
        *,
        owner_user_id: UUID,
        name: str | None,
        official_identifiers: list[str] | None,
        selected_company_id: UUID | None,
        identity_evidence_refs: list[str] | None,
        idempotency_key: str,
    ) -> dict[str, object]:
        del owner_user_id, name, official_identifiers, identity_evidence_refs, idempotency_key
        with self._session_factory() as session:
            company = session.get(Company, selected_company_id)
        if company is None:
            return {"resolution": "selection_required", "company_id": None, "candidates": []}
        return {"resolution": "resolved", "company_id": str(company.company_id), "candidates": []}

    def import_job_posting(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> JobPostingImportAccepted:
        del analysis_request_id, idempotency_key
        if company_id != COMPANY_ID or url != SOURCE_URL:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        source_id = uuid4()
        posting_id = uuid4()
        policy = _policy(uuid4())
        job_id = uuid4()
        with self._session_factory.begin() as session:
            session.add_all(
                [
                    Source(
                        source_id=source_id,
                        company_id=company_id,
                        source_type=SourceType.JOB_POSTING.value,
                        canonical_url=url,
                        title="Synthetic static posting",
                    ),
                    SourcePolicyDecision(
                        policy_decision_id=policy.policy_decision_id,
                        source_id=source_id,
                        revision=1,
                        official_status=policy.official_status.value,
                        access_class=policy.access_class.value,
                        collection_permission=policy.collection_permission.value,
                        excerpt_storage_permission=policy.excerpt_storage_permission.value,
                        body_storage_permission=policy.body_storage_permission.value,
                        redistribution_permission=policy.redistribution_permission.value,
                        evidence_refs=["fixture:static_posting"],
                        checked_at=policy.checked_at,
                        policy_version=policy.policy_version,
                    ),
                ]
            )
            resolve_job_posting(
                session,
                candidate_job_posting_id=posting_id,
                source_id=source_id,
                company_id=company_id,
            )
        self._source_id = source_id
        self._posting_id = posting_id
        self._policy = policy
        self._source_owners[source_id] = owner_user_id
        self._posting_jobs[posting_id] = job_id
        self._job_owners[job_id] = owner_user_id
        self._dispatch(job_id)
        return JobPostingImportAccepted(posting_id, source_id, company_id, job_id)

    def find_owned_job_posting(
        self, *, owner_user_id: UUID, job_posting_id: UUID
    ) -> JobPostingRecord | None:
        if self._posting_jobs.get(job_posting_id) is None:
            return None
        job_id = self._posting_jobs[job_posting_id]
        if self._job_owners[job_id] != owner_user_id:
            return None
        with self._session_factory() as session:
            posting = session.get(JobPosting, job_posting_id)
            if posting is None:
                return None
            source = session.get(Source, posting.source_id)
        if source is None:
            return None
        return JobPostingRecord(
            job_posting_id=posting.job_posting_id,
            company_id=posting.company_id,
            source_id=posting.source_id,
            current_source_version_id=source.current_source_version_id,
        )

    def list_job_posting_content(
        self, *, source_id: UUID, company_id: UUID
    ) -> tuple[JobPostingContentRecord, ...]:
        with self._session_factory() as session:
            versions = session.scalars(
                select(SourceVersion).where(
                    SourceVersion.source_id == source_id,
                    SourceVersion.company_id == company_id,
                )
            ).all()
            records: list[JobPostingContentRecord] = []
            for version in versions:
                evidence = tuple(
                    JobPostingEvidenceSnapshot(
                        evidence_id=row.evidence_id,
                        source_version_id=row.source_version_id,
                        text_excerpt=row.text_excerpt,
                    )
                    for row in session.scalars(
                        select(Evidence)
                        .where(Evidence.source_version_id == version.source_version_id)
                        .order_by(Evidence.chunk_order, Evidence.evidence_id)
                    )
                )
                for revision in session.scalars(
                    select(ExtractionRevision).where(
                        ExtractionRevision.source_version_id == version.source_version_id
                    )
                ):
                    sections = tuple(
                        self._section_value(session, row)
                        for row in session.scalars(
                            select(PostingSection)
                            .where(
                                PostingSection.extraction_revision_id
                                == revision.extraction_revision_id
                            )
                            .order_by(PostingSection.order, PostingSection.section_key)
                        )
                    )
                    records.append(
                        JobPostingContentRecord(
                            source_version_id=version.source_version_id,
                            source_id=version.source_id,
                            company_id=version.company_id,
                            title=version.title,
                            extraction_revision_id=revision.extraction_revision_id,
                            created_at=revision.created_at,
                            extraction_status=ExtractionStatus(revision.extraction_status),
                            posting_sections=sections,
                            limitations=tuple(str(item) for item in revision.limitations),
                            evidence=evidence,
                        )
                    )
        return tuple(records)

    @staticmethod
    def _section_value(session: Session, row: PostingSection) -> PostingSectionValue:
        evidence_ids = session.scalars(
            select(PostingSectionEvidence.evidence_id)
            .where(
                PostingSectionEvidence.extraction_revision_id == row.extraction_revision_id,
                PostingSectionEvidence.section_key == row.section_key,
            )
            .order_by(PostingSectionEvidence.evidence_order)
        ).all()
        return PostingSectionValue(
            section_key=row.section_key,
            kind=PostingSectionKind(row.kind),
            heading_raw=row.heading_raw,
            text_raw=row.text_raw,
            evidence_ids=list(evidence_ids),
            order=row.order,
            relation_text=row.relation_text,
        )

    def read_failure_reference(
        self, *, owner_user_id: UUID, job_posting_id: UUID
    ) -> JobPostingFailureReference | None:
        del owner_user_id, job_posting_id
        return None

    def poll_job(self, *, owner_user_id: UUID, job_id: UUID) -> dict[str, object] | None:
        if self._job_owners.get(job_id) != owner_user_id:
            return None
        result = self._results[job_id]
        return {
            "job_id": str(job_id),
            "source_id": str(result.source_id),
            "status": "COMPLETE" if not result.failures else "FAILED",
        }

    def find_owned_source(
        self, *, owner_user_id: UUID, source_id: UUID
    ) -> OwnedSourceRecord | None:
        if self._source_owners.get(source_id) != owner_user_id:
            return None
        with self._session_factory() as session:
            source = session.get(Source, source_id)
        return None if source is None else OwnedSourceRecord(source.source_id, source.company_id)

    def find_owned_source_version(
        self, *, owner_user_id: UUID, source_version_id: UUID
    ) -> OwnedSourceVersionRecord | None:
        with self._session_factory() as session:
            version = session.get(SourceVersion, source_version_id)
        if version is None or self._source_owners.get(version.source_id) != owner_user_id:
            return None
        return OwnedSourceVersionRecord(version.source_id, version.source_version_id)

    def find_owned_source_version_revision(
        self,
        *,
        owner_user_id: UUID,
        source_version_id: UUID,
        extraction_revision_id: UUID,
    ) -> OwnedSourceVersionRecord | None:
        version = self.find_owned_source(
            owner_user_id=owner_user_id,
            source_id=self._source_id or uuid4(),
        )
        if version is None:
            return None
        with self._session_factory() as session:
            revision = session.get(ExtractionRevision, extraction_revision_id)
        if revision is None or revision.source_version_id != source_version_id:
            return None
        return self.find_owned_source_version(
            owner_user_id=owner_user_id,
            source_version_id=source_version_id,
        )

    def current_state(self, *, source_id: UUID) -> SourceCurrentReadState:
        assert source_id == self._source_id
        return SourceCurrentReadState({"state": "current"}, FreshnessStatus.CURRENT)

    def list_source_versions(
        self,
        *,
        source_id: UUID,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[SourceVersionReadRecord, ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(SourceVersion).where(SourceVersion.source_id == source_id)
            ).all()
        records = sorted(
            (
                SourceVersionReadRecord(
                    source_id=row.source_id,
                    source_version_id=row.source_version_id,
                    title=row.title,
                    source_type=row.source_type,
                    canonical_url=row.canonical_url,
                    content_hash=row.content_hash,
                    hash_profile_version=row.hash_profile_version,
                    representation=row.representation,
                    collected_at=row.collected_at,
                    published_at=cast(Any, row.published_at),
                )
                for row in rows
            ),
            key=lambda row: (row.collected_at, row.source_version_id),
            reverse=True,
        )
        if before is not None:
            records = [row for row in records if (row.collected_at, row.source_version_id) < before]
        return tuple(records[:limit])

    def list_revision_evidence(
        self,
        *,
        source_version_id: UUID,
        extraction_revision_id: UUID,
        after: tuple[int, UUID] | None,
        limit: int,
    ) -> tuple[EvidenceReadRecord, ...] | None:
        with self._session_factory() as session:
            revision = session.get(ExtractionRevision, extraction_revision_id)
            if revision is None or revision.source_version_id != source_version_id:
                return None
            rows = session.scalars(
                select(Evidence)
                .where(Evidence.source_version_id == source_version_id)
                .order_by(Evidence.chunk_order, Evidence.evidence_id)
            ).all()
        records = [
            EvidenceReadRecord(
                evidence_id=row.evidence_id,
                source_version_id=row.source_version_id,
                section_title=row.section_title,
                text_excerpt=row.text_excerpt,
                locator=row.locator,
                chunk_order=row.chunk_order,
                origin_kind=row.origin_kind,
            )
            for row in rows
        ]
        if after is not None:
            records = [row for row in records if (row.chunk_order, row.evidence_id) > after]
        return tuple(records[:limit])

    def list_origin_relations_for_evidence(
        self, *, source_id: UUID, evidence_ids: tuple[UUID, ...]
    ) -> tuple[object, ...]:
        assert source_id == self._source_id
        del evidence_ids
        return ()

    def authorize_current(self, *, source_id: UUID, operation: PolicyOperation) -> None:
        with self._session_factory() as session:
            decision = session.scalars(
                select(SourcePolicyDecision)
                .where(SourcePolicyDecision.source_id == source_id)
                .order_by(SourcePolicyDecision.revision.desc())
            ).first()
        assert decision is not None
        permission = {
            PolicyOperation.FETCH: decision.collection_permission,
            PolicyOperation.STORE_EXCERPT: decision.excerpt_storage_permission,
            PolicyOperation.REDISTRIBUTE: decision.redistribution_permission,
            PolicyOperation.STORE_BODY: decision.body_storage_permission,
        }[operation]
        if permission != Permission.ALLOWED.value:
            raise PolicyBlocked(f"current policy blocks {operation.value}")

    def accept_policy_refresh(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
        analysis_request_id: UUID | None,
        idempotency_key: str,
        operation: str,
    ) -> SourceRefreshAccepted:
        del analysis_request_id, operation
        assert self._source_owners.get(source_id) == owner_user_id
        if accepted := self._refreshes.get(idempotency_key):
            return accepted
        accepted = SourceRefreshAccepted(source_id=source_id, job_id=uuid4())
        self._refreshes[idempotency_key] = accepted
        self._job_owners[accepted.job_id] = owner_user_id
        self._dispatch(accepted.job_id)
        return accepted

    def deny_current_evidence_policy(self) -> None:
        assert self._source_id is not None
        with self._session_factory.begin() as session:
            session.add(
                SourcePolicyDecision(
                    policy_decision_id=uuid4(),
                    source_id=self._source_id,
                    revision=2,
                    official_status=OfficialStatus.VERIFIED.value,
                    access_class=AccessClass.PUBLIC.value,
                    collection_permission=Permission.ALLOWED.value,
                    excerpt_storage_permission=Permission.DENIED.value,
                    body_storage_permission=Permission.DENIED.value,
                    redistribution_permission=Permission.DENIED.value,
                    evidence_refs=["fixture:current-policy-denied"],
                    checked_at=NOW,
                    policy_version="static-slice.integration.v2",
                )
            )

    def _dispatch(self, job_id: UUID) -> None:
        assert self._source_id is not None
        assert self._policy is not None
        input_version = self.dispatches + 1
        command = CollectionCommand(
            schema_version="w2.collection.v1",
            command_id=uuid4(),
            job_id=job_id,
            authenticated_owner_ref=OWNER_ID,
            project_ref=str(PROJECT_ID),
            company_id=COMPANY_ID,
            source_id=self._source_id,
            input_version=input_version,
            execution_fence=f"static-slice-{job_id}",
            purpose_ref=uuid4(),
            core_source_decision=CoreSourceDecision(
                is_core=True,
                decided_by="integration-test",
                rationale="explicit static posting import",
                decision_revision=1,
                analysis_input_version=input_version,
            ),
            resume_stage=CollectionStage.POLICY,
            policy_revision=None,
            owner_deletion_epoch=0,
        )
        input_value = StaticCollectionInput(
            source_id=self._source_id,
            company_id=COMPANY_ID,
            source_url=SOURCE_URL,
            title="Synthetic static posting",
            source_type=SourceType.JOB_POSTING,
            policy=self._policy,
            policy_revision=1,
            robots_permission=Permission.ALLOWED,
            limits=_limits(),
            result_version=input_version,
            aggregate_revision=input_version,
            language="en",
        )
        permit = WorkerExecutionPermit(
            attempt_id=uuid4(),
            command=command,
            checkpoint_ref=None,
            all_core_decisions_received=True,
            slot_acquired=True,
            retry_not_before=None,
        )
        control = _W1Harness(permit)
        collector = _NoNetworkCollector(self._document)
        worker = SourceCollectionWorker(
            control=control,
            execution_factory=lambda issued: StaticCollectionExecution(
                attempt_id=issued.attempt_id,
                input_provider=_InputProvider(input_value),
                collector=collector,
                parser=extract_static_candidate,
                clock=lambda: NOW,
                uuid_factory=uuid4,
            ),
            session_factory=self._session_factory,
            lock_authority=self._lock_authority,
            committer=commit_prepared_collection,
            replayer=replay_committed_collection,
            clock=lambda: NOW,
        )
        self.dispatches += 1
        result = worker.handle(command.model_dump(mode="json"))
        assert control.finalized is result
        assert collector.requests
        self._results[job_id] = result

    @staticmethod
    def _lock_authority(
        session: Session,
        *,
        command: CollectionCommand,
        attempt_id: UUID,
    ) -> ExecutionAuthorityGrant:
        session.execute(
            select(Source.source_id).where(Source.source_id == command.source_id)
        ).scalar_one()
        return ExecutionAuthorityGrant(
            attempt_id=attempt_id,
            owner_user_id=command.authenticated_owner_ref,
            job_id=command.job_id,
            command_id=command.command_id,
            company_id=command.company_id,
            source_id=command.source_id,
            input_version=command.input_version,
            execution_fence=command.execution_fence,
            owner_deletion_epoch=command.owner_deletion_epoch,
            pointer_eligible=True,
            private_scope=_trusted_scope(command),
        )


def _apps(slice_harness: _StaticPostingSlice) -> tuple[FastAPI, FastAPI, FastAPI]:
    company_app = create_company_source_api(
        service=slice_harness,
        authenticator=lambda _request: AuthenticatedPrincipal(OWNER_ID),
        settings=SETTINGS,
        cursor_codec=CursorCodec("x" * 32, SETTINGS),
    )
    posting_app = create_job_posting_api(
        service=JobPostingService(repository=slice_harness, job_port=slice_harness),
        authenticator=lambda request: _authenticate(request),
        settings=SETTINGS,
    )

    @posting_app.get("/api/v1/jobs/{job_id}")
    def get_job_status(job_id: UUID, request: Request) -> dict[str, object]:
        principal = _authenticate(request)
        if principal is None:
            raise ApiProblem(ApiErrorCode.AUTHENTICATION_REQUIRED)
        result = slice_harness.poll_job(owner_user_id=principal.user_id, job_id=job_id)
        if result is None:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        return result

    version_app = create_version_evidence_api(
        service=SourceVersionEvidenceService(
            ownership_port=slice_harness,
            policy_port=slice_harness,
            read_port=slice_harness,
            refresh_job_port=slice_harness,
        ),
        authenticator=lambda request: _authenticate(request),
        settings=SETTINGS,
        cursor_codec=CursorCodec("y" * 32, SETTINGS),
    )
    return company_app, posting_app, version_app


def _authenticate(request: Request) -> AuthenticatedPrincipal | None:
    if request.headers.get("authorization") == "Bearer static-slice-owner":
        return AuthenticatedPrincipal(OWNER_ID)
    return None


def _source_counts(
    session_factory: sessionmaker[Session], source_id: UUID
) -> tuple[int, int, int, int]:
    with session_factory() as session:
        version_count = session.scalar(
            select(func.count())
            .select_from(SourceVersion)
            .where(SourceVersion.source_id == source_id)
        )
        revision_count = session.scalar(
            select(func.count())
            .select_from(ExtractionRevision)
            .join(SourceVersion)
            .where(SourceVersion.source_id == source_id)
        )
        evidence_count = session.scalar(
            select(func.count())
            .select_from(Evidence)
            .join(SourceVersion)
            .where(SourceVersion.source_id == source_id)
        )
        version_event_count = session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == source_id,
                OutboxEvent.event_type == SourceEventType.VERSION_AVAILABLE.value,
            )
        )
    return (
        cast(int, version_count),
        cast(int, revision_count),
        cast(int, evidence_count),
        cast(int, version_event_count),
    )


def test_static_posting_refresh_slice_preserves_versions_evidence_and_current_pointer(
    session_factory: sessionmaker[Session],
) -> None:
    slice_harness = _StaticPostingSlice(session_factory)
    company_app, posting_app, version_app = _apps(slice_harness)

    resolved = _request(
        company_app,
        "POST",
        "/api/v1/companies/resolve",
        headers=_headers("static-slice-resolve"),
        body={"selected_company_id": str(COMPANY_ID), "identity_evidence_refs": []},
    )
    assert resolved.status_code == 200
    assert resolved.payload()["resolution"] == "resolved"
    assert resolved.payload()["company_id"] == str(COMPANY_ID)

    imported = _request(
        posting_app,
        "POST",
        "/api/v1/job-postings/import",
        headers=_headers("static-slice-import-a"),
        body={"company_id": str(COMPANY_ID), "url": SOURCE_URL, "analysis_request_id": "t045"},
    )
    assert imported.status_code == 202
    accepted = imported.payload()
    source_id = UUID(cast(str, accepted["source_id"]))
    posting_id = UUID(cast(str, accepted["job_posting_id"]))
    job_id = UUID(cast(str, accepted["job_id"]))
    status = _request(
        posting_app,
        "GET",
        cast(str, accepted["status_url"]),
        headers=_headers(),
    )
    assert status.payload()["status"] == "COMPLETE"

    posting_a = _request(
        posting_app,
        "GET",
        f"/api/v1/job-postings/{posting_id}",
        headers=_headers(),
    )
    assert posting_a.status_code == 200
    public_a = cast(dict[str, Any], posting_a.payload()["job_posting"])
    required_texts = [
        section["text"] for section in public_a["sections"] if section["kind"] == "required"
    ]
    assert any("AWS experience is required." in text for text in required_texts)
    assert not any("GCP" in text for text in required_texts)
    version_a = UUID(cast(str, public_a["source_version_id"]))

    with session_factory() as session:
        revision_a = session.scalar(
            select(ExtractionRevision.extraction_revision_id).where(
                ExtractionRevision.source_version_id == version_a
            )
        )
        persisted_a = session.get(SourceVersion, version_a)
        evidence_a = session.scalars(
            select(Evidence)
            .where(Evidence.source_version_id == version_a)
            .order_by(Evidence.chunk_order, Evidence.evidence_id)
        ).all()
        retained_body_count = session.scalar(
            select(func.count())
            .select_from(RetainedBody)
            .where(
                RetainedBody.source_id == source_id,
                RetainedBody.source_version_id == version_a,
            )
        )
    assert revision_a is not None
    assert persisted_a is not None
    assert persisted_a.published_at["status"] == "unknown"
    assert persisted_a.published_at["value"] is None
    assert persisted_a.collected_at == NOW
    assert retained_body_count == 0
    assert any("AWS experience is required." in row.text_excerpt for row in evidence_a)
    assert all(row.locator["kind"] == "xpath" for row in evidence_a)
    assert all(isinstance(row.locator["value"], str) for row in evidence_a)
    counts_a = _source_counts(session_factory, source_id)
    assert counts_a[:3] == (1, 1, len(evidence_a))
    assert counts_a[3] == 1

    versions = _request(
        version_app,
        "GET",
        f"/api/v1/sources/{source_id}/versions",
        headers=_headers(),
    )
    assert versions.status_code == 200
    assert [item["source_version_id"] for item in versions.payload()["items"]] == [str(version_a)]
    evidence_response = _request(
        version_app,
        "GET",
        f"/api/v1/source-versions/{version_a}/evidence?extraction_revision_id={revision_a}",
        headers=_headers(),
    )
    assert evidence_response.status_code == 200
    assert evidence_response.payload()["retention_scope"] == "excerpts_only"
    assert evidence_response.payload()["body_ref"] is None
    assert any(
        "AWS experience is required." in item["text_excerpt"]
        for item in evidence_response.payload()["items"]
    )

    unchanged = _request(
        version_app,
        "POST",
        f"/api/v1/sources/{source_id}/refresh",
        headers=_headers("static-slice-refresh-a-new"),
        body={},
    )
    assert unchanged.status_code == 202
    assert UUID(cast(str, unchanged.payload()["job_id"])) != job_id
    assert _source_counts(session_factory, source_id) == (1, 1, len(evidence_a), 2)

    replay = _request(
        version_app,
        "POST",
        f"/api/v1/sources/{source_id}/refresh",
        headers=_headers("static-slice-refresh-a-new"),
        body={},
    )
    assert replay.status_code == 202
    assert replay.payload() == unchanged.payload()
    assert slice_harness.dispatches == 2
    assert _source_counts(session_factory, source_id) == (1, 1, len(evidence_a), 2)

    slice_harness._document = HTML_B
    changed = _request(
        version_app,
        "POST",
        f"/api/v1/sources/{source_id}/refresh",
        headers=_headers("static-slice-refresh-b"),
        body={},
    )
    assert changed.status_code == 202
    with session_factory() as session:
        source_after_b = session.get(Source, source_id)
        versions_after_b = session.scalars(
            select(SourceVersion).where(SourceVersion.source_id == source_id)
        ).all()
        version_b = next(row for row in versions_after_b if row.source_version_id != version_a)
        revision_b = session.scalar(
            select(ExtractionRevision.extraction_revision_id).where(
                ExtractionRevision.source_version_id == version_b.source_version_id
            )
        )
    assert source_after_b is not None
    assert revision_b is not None
    assert source_after_b.current_source_version_id == version_b.source_version_id
    assert _source_counts(session_factory, source_id) == (2, 2, len(evidence_a) * 2, 3)

    historical_a = _request(
        version_app,
        "GET",
        f"/api/v1/source-versions/{version_a}/evidence?extraction_revision_id={revision_a}",
        headers=_headers(),
    )
    assert historical_a.status_code == 200
    assert historical_a.payload() == evidence_response.payload()

    slice_harness._document = HTML_A
    returned = _request(
        version_app,
        "POST",
        f"/api/v1/sources/{source_id}/refresh",
        headers=_headers("static-slice-refresh-a-return"),
        body={},
    )
    assert returned.status_code == 202
    with session_factory() as session:
        source_after_return = session.get(Source, source_id)
    assert source_after_return is not None
    assert source_after_return.current_source_version_id == version_a
    assert _source_counts(session_factory, source_id) == (2, 2, len(evidence_a) * 2, 4)

    slice_harness.deny_current_evidence_policy()
    denied = _request(
        version_app,
        "GET",
        f"/api/v1/source-versions/{version_a}/evidence?extraction_revision_id={revision_a}",
        headers=_headers(),
    )
    assert denied.status_code == 403
