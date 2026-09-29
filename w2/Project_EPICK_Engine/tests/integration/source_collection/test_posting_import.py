"""T036 job-posting import/retry assembly against approved PostgreSQL.

W1 import, job status, and retry ownership are deliberately test-local until
the real W1 adapter exists. W2 collection, parsing, normalized persistence,
and the public job-posting API use production code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode
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
    create_job_posting_api,
)
from epick_engine.source_collection.collector import (
    StaticFetchFailureCode,
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
    OfficialStatus,
    Permission,
    Policy,
    PostingSectionKind,
    SourceType,
)
from epick_engine.source_collection.contracts import (
    PostingSection as PostingSectionValue,
)
from epick_engine.source_collection.parsing import extract_static_candidate
from epick_engine.source_collection.persistence import (
    Base,
    CollectionAttempt,
    Company,
    Evidence,
    ExecutionAuthorityGrant,
    ExtractionRevision,
    JobPosting,
    OutboxEvent,
    PersistenceConflict,
    PostingSection,
    PostingSectionEvidence,
    RequestDeduplication,
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
from epick_engine.source_collection.persistence import (
    resolve_request_deduplication as _resolve_request_deduplication,
)
from epick_engine.source_collection.policy import (
    ExecutionLimits,
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
    JobPostingContentRecord,
    JobPostingEvidenceSnapshot,
    JobPostingFailureReference,
    JobPostingImportAccepted,
    JobPostingRecord,
    JobPostingRetryResult,
    JobPostingSelectionInvalid,
    JobPostingService,
    StaticCollectionExecution,
    StaticCollectionInput,
)
from epick_engine.source_collection.worker import (
    SourceCollectionWorker,
    WorkerExecutionPermit,
)

pytestmark = pytest.mark.approved_postgres

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
OWNER_ID = UUID("00000000-0000-4000-8000-000000003601")
COMPANY_ID = UUID("00000000-0000-4000-8000-000000003602")
PROJECT_ID = UUID("00000000-0000-4000-8000-000000003603")


def _trusted_scope(command: CollectionCommand) -> PrivateWriteScope:
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=command.authenticated_owner_ref,
            owner_deletion_epoch=command.owner_deletion_epoch,
            scope=PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_ID),
            authority_ref="w1:test-posting-import-authority",
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


def resolve_request_deduplication(session, **kwargs):
    owner_user_id = kwargs["owner_user_id"]
    kwargs.setdefault(
        "private_scope",
        PrivateWriteScope(
            PrivateWriteAuthorityDecision(
                owner_user_id=owner_user_id,
                owner_deletion_epoch=0,
                scope=PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_ID),
                authority_ref="w1:test-posting-import-dedup-authority",
            )
        ),
    )
    return _resolve_request_deduplication(session, **kwargs)


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic_sources"
ANNOTATIONS = cast(
    dict[str, Any],
    json.loads((FIXTURE_DIR / "annotated_postings.json").read_text(encoding="utf-8")),
)
SOURCE_URL = cast(str, ANNOTATIONS["source_url"])
HTML = (FIXTURE_DIR / cast(str, ANNOTATIONS["fixture_path"])).read_text(encoding="utf-8")
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
    """Give each import assembly test an isolated PostgreSQL schema."""
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_posting_import_{uuid4().hex}"
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


def _authenticate(request: Request) -> AuthenticatedPrincipal | None:
    if request.headers.get("authorization") == "Bearer posting-owner":
        return AuthenticatedPrincipal(user_id=OWNER_ID)
    return None


def _complete(awaitable: Awaitable[Any]) -> Any:
    return asyncio.run(awaitable)


def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
) -> _AsgiResponse:
    request_body = b"" if body is None else json.dumps(body).encode("utf-8")
    raw_headers = [(b"host", b"testserver")]
    raw_headers.extend(
        (name.lower().encode("ascii"), value.encode("utf-8"))
        for name, value in (headers or {}).items()
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
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": urlencode({}).encode("ascii"),
            "headers": raw_headers,
            "client": ("test-client", 1),
            "server": ("testserver", 80),
        },
    )
    delivered = False
    messages: list[Message] = []

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
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
    headers = {"authorization": "Bearer posting-owner"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


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
        redistribution_permission=Permission.DENIED,
        checked_at=NOW,
        policy_version="posting-import.integration.v1",
    )


def _command(
    *,
    source_id: UUID,
    job_id: UUID,
    input_version: int,
    resume_stage: CollectionStage,
    policy_revision: int | None,
) -> CollectionCommand:
    return CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=uuid4(),
        job_id=job_id,
        authenticated_owner_ref=OWNER_ID,
        project_ref=str(PROJECT_ID),
        company_id=COMPANY_ID,
        source_id=source_id,
        input_version=input_version,
        execution_fence=f"posting-import-fence-{input_version}",
        purpose_ref=uuid4(),
        core_source_decision=CoreSourceDecision(
            is_core=True,
            decided_by="integration-test",
            rationale="explicit job-posting import",
            decision_revision=1,
            analysis_input_version=input_version,
        ),
        resume_stage=resume_stage,
        policy_revision=policy_revision,
        owner_deletion_epoch=0,
    )


class _NoNetworkCollector:
    """Return deterministic fixture outcomes without opening a socket."""

    def __init__(self, outcomes: list[StaticFetchFailureCode | None]) -> None:
        self._outcomes = outcomes
        self.requests: list[StaticFetchRequest] = []

    def fetch(self, request: StaticFetchRequest) -> StaticFetchResult:
        outcome_index = len(self.requests)
        self.requests.append(request)
        outcome = self._outcomes[outcome_index]
        if outcome is not None:
            return StaticFetchResult(
                command_id=request.command_id,
                candidate=None,
                failure_code=outcome,
                retry_after="0" if outcome is StaticFetchFailureCode.RATE_LIMITED else None,
            )
        candidate = StaticResponseCandidate(
            final_target=ValidatedTarget(
                url=request.source_url,
                hostname="synthetic-meridian-careers.test",
                port=443,
                resolved_addresses=frozenset({"198.51.100.20"}),
            ),
            representation=Representation.HTML,
            document=UntrustedDocument(HTML),
            http_status=200,
            raw_size=len(HTML.encode("utf-8")),
            decompressed_size=len(HTML.encode("utf-8")),
        )
        return StaticFetchResult(
            command_id=request.command_id,
            candidate=candidate,
            failure_code=None,
        )

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class _InputProvider:
    value: StaticCollectionInput

    def load(self, _command: CollectionCommand) -> StaticCollectionInput:
        return self.value


class _WorkerControl:
    def __init__(self, permit: WorkerExecutionPermit) -> None:
        self.permit = permit
        self.stages: list[CollectionStage] = []
        self.finalized: CollectionResult | None = None

    def authorize_execution(self, command: CollectionCommand) -> WorkerExecutionPermit:
        assert command == self.permit.command
        return self.permit

    def authorize_and_record_stage(
        self,
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        *,
        policy_revision: int | None = None,
    ) -> WorkerExecutionPermit:
        self.stages.append(stage)
        if policy_revision is not None and permit.command.policy_revision != policy_revision:
            permit = replace(
                permit,
                command=permit.command.model_copy(update={"policy_revision": policy_revision}),
            )
        self.permit = permit
        return permit

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
        raise AssertionError("the deterministic worker must finish through its finalizer")


class _PostingAssembly:
    """Test-local W1 adapter plus a PostgreSQL-backed W2 read repository."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        outcomes: list[StaticFetchFailureCode | None],
    ) -> None:
        self._session_factory = session_factory
        self.collector = _NoNetworkCollector(outcomes)
        self.dispatches = 0
        self.stage_histories: list[tuple[CollectionStage, ...]] = []
        self._job_owners: dict[UUID, UUID] = {}
        self._posting_jobs: dict[UUID, UUID] = {}
        self._job_sources: dict[UUID, UUID] = {}
        self._results: dict[UUID, CollectionResult] = {}
        self._policy_by_source: dict[UUID, Policy] = {}
        with self._session_factory.begin() as session:
            session.add(
                Company(
                    company_id=COMPANY_ID,
                    legal_name="Synthetic Meridian Careers Ltd.",
                    aliases=["Synthetic Meridian Careers"],
                    official_domains=["synthetic-meridian-careers.test"],
                    legal_identifiers={},
                    identity_status="verified",
                    identity_evidence=["fixture:synthetic_static_posting_v1"],
                )
            )

    def import_job_posting(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> JobPostingImportAccepted:
        payload = {
            "company_id": str(company_id),
            "url": url,
            "analysis_request_id": analysis_request_id,
        }
        request_hash = _request_hash(payload)
        operation = "job-posting-import"
        replay = False
        accepted: dict[str, str]
        try:
            with self._session_factory.begin() as session:
                existing = session.scalar(
                    select(RequestDeduplication)
                    .where(
                        RequestDeduplication.owner_user_id == owner_user_id,
                        RequestDeduplication.operation == operation,
                        RequestDeduplication.idempotency_key == idempotency_key,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    replay = True
                    row = resolve_request_deduplication(
                        session,
                        owner_user_id=owner_user_id,
                        operation=operation,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        accepted_resource_ref=existing.accepted_resource_ref,
                        input_version=1,
                        created_at=NOW,
                    )
                    accepted = cast(dict[str, str], json.loads(row.accepted_resource_ref))
                else:
                    source = session.scalar(
                        select(Source).where(
                            Source.company_id == company_id,
                            Source.canonical_url == url,
                        )
                    )
                    if source is None:
                        source = Source(
                            source_id=uuid4(),
                            company_id=company_id,
                            source_type=SourceType.JOB_POSTING.value,
                            canonical_url=url,
                            title="Synthetic static posting",
                        )
                        policy = _policy(uuid4())
                        session.add_all(
                            [
                                source,
                                SourcePolicyDecision(
                                    policy_decision_id=policy.policy_decision_id,
                                    source_id=source.source_id,
                                    revision=1,
                                    official_status=policy.official_status.value,
                                    access_class=policy.access_class.value,
                                    collection_permission=policy.collection_permission.value,
                                    excerpt_storage_permission=policy.excerpt_storage_permission.value,
                                    body_storage_permission=policy.body_storage_permission.value,
                                    redistribution_permission=policy.redistribution_permission.value,
                                    evidence_refs=["fixture:synthetic_static_posting_v1"],
                                    checked_at=policy.checked_at,
                                    policy_version=policy.policy_version,
                                ),
                            ]
                        )
                        session.flush()
                        self._policy_by_source[source.source_id] = policy
                    posting = resolve_job_posting(
                        session,
                        candidate_job_posting_id=uuid4(),
                        source_id=source.source_id,
                        company_id=company_id,
                    )
                    job_id = uuid4()
                    accepted = {
                        "job_posting_id": str(posting.job_posting_id),
                        "source_id": str(source.source_id),
                        "company_id": str(company_id),
                        "job_id": str(job_id),
                    }
                    resolve_request_deduplication(
                        session,
                        owner_user_id=owner_user_id,
                        operation=operation,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        accepted_resource_ref=json.dumps(accepted, sort_keys=True),
                        input_version=1,
                        created_at=NOW,
                    )
        except PersistenceConflict as exc:
            raise ApiProblem(ApiErrorCode.IDEMPOTENCY_CONFLICT) from exc

        posting_id = UUID(accepted["job_posting_id"])
        source_id = UUID(accepted["source_id"])
        job_id = UUID(accepted["job_id"])
        if replay:
            return JobPostingImportAccepted(posting_id, source_id, company_id, job_id)
        self._job_owners[job_id] = owner_user_id
        self._posting_jobs[posting_id] = job_id
        self._job_sources[job_id] = source_id
        self._dispatch(
            _command(
                source_id=source_id,
                job_id=job_id,
                input_version=1,
                resume_stage=CollectionStage.POLICY,
                policy_revision=None,
            ),
            checkpoint_ref=None,
            retry_not_before=None,
        )
        return JobPostingImportAccepted(posting_id, source_id, company_id, job_id)

    def retry_job_posting(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
        job_id: UUID,
        expected_input_version: int,
        expected_result_version: int,
        idempotency_key: str,
    ) -> JobPostingRetryResult:
        if self._job_owners.get(job_id) != owner_user_id:
            raise JobPostingSelectionInvalid("job is not owned by the requesting user")
        if self._posting_jobs.get(job_posting_id) != job_id:
            raise JobPostingSelectionInvalid("job does not belong to the posting")
        payload = {
            "job_id": str(job_id),
            "expected_input_version": expected_input_version,
            "expected_result_version": expected_result_version,
        }
        request_hash = _request_hash(payload)
        operation = f"job-posting-retry:{job_posting_id}"
        previous = self._results[job_id]
        accepted: dict[str, str]
        replay = False
        try:
            with self._session_factory.begin() as session:
                existing = session.scalar(
                    select(RequestDeduplication)
                    .where(
                        RequestDeduplication.owner_user_id == owner_user_id,
                        RequestDeduplication.operation == operation,
                        RequestDeduplication.idempotency_key == idempotency_key,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    replay = True
                else:
                    if (
                        previous.input_version != expected_input_version
                        or previous.result_version != expected_result_version
                        or previous.resume_stage is None
                    ):
                        raise JobPostingSelectionInvalid(
                            "retry versions or resume stage are invalid"
                        )
                    accepted = {
                        "job_posting_id": str(job_posting_id),
                        "job_id": str(job_id),
                        "resume_stage": previous.resume_stage.value,
                    }
                row = resolve_request_deduplication(
                    session,
                    owner_user_id=owner_user_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    accepted_resource_ref=(
                        existing.accepted_resource_ref
                        if existing is not None
                        else json.dumps(accepted, sort_keys=True)
                    ),
                    input_version=expected_input_version + 1,
                    created_at=NOW,
                )
                accepted = cast(dict[str, str], json.loads(row.accepted_resource_ref))
        except PersistenceConflict as exc:
            raise ApiProblem(ApiErrorCode.IDEMPOTENCY_CONFLICT) from exc
        resume_stage = CollectionStage(accepted["resume_stage"])
        if not replay:
            self._dispatch(
                _command(
                    source_id=self._job_sources[job_id],
                    job_id=job_id,
                    input_version=expected_input_version + 1,
                    resume_stage=resume_stage,
                    policy_revision=previous.policy_revision,
                ),
                checkpoint_ref=previous.checkpoint_ref,
                retry_not_before=previous.retry_not_before,
            )
        return JobPostingRetryResult(
            job_posting_id=job_posting_id,
            job_id=job_id,
            resume_stage=resume_stage,
        )

    def read_failure_reference(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
    ) -> JobPostingFailureReference | None:
        job_id = self._posting_jobs.get(job_posting_id)
        if job_id is None or self._job_owners.get(job_id) != owner_user_id:
            return None
        result = self._results[job_id]
        if not result.failures:
            return None
        return JobPostingFailureReference(
            failure=result.failures[0],
            checkpoint_ref=result.checkpoint_ref,
            required_actions=tuple(result.required_actions),
        )

    def find_owned_job_posting(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
    ) -> JobPostingRecord | None:
        job_id = self._posting_jobs.get(job_posting_id)
        if job_id is None or self._job_owners.get(job_id) != owner_user_id:
            return None
        with self._session_factory() as session:
            row = session.get(JobPosting, job_posting_id)
            if row is None:
                return None
            source = session.get(Source, row.source_id)
            assert source is not None
            return JobPostingRecord(
                job_posting_id=row.job_posting_id,
                company_id=row.company_id,
                source_id=row.source_id,
                current_source_version_id=source.current_source_version_id,
            )

    def list_job_posting_content(
        self,
        *,
        source_id: UUID,
        company_id: UUID,
    ) -> tuple[JobPostingContentRecord, ...]:
        records: list[JobPostingContentRecord] = []
        with self._session_factory() as session:
            versions = session.scalars(
                select(SourceVersion).where(
                    SourceVersion.source_id == source_id,
                    SourceVersion.company_id == company_id,
                )
            ).all()
            for version in versions:
                evidence_rows = session.scalars(
                    select(Evidence)
                    .where(Evidence.source_version_id == version.source_version_id)
                    .order_by(Evidence.chunk_order, Evidence.evidence_id)
                ).all()
                evidence = tuple(
                    JobPostingEvidenceSnapshot(
                        evidence_id=row.evidence_id,
                        source_version_id=row.source_version_id,
                        text_excerpt=row.text_excerpt,
                    )
                    for row in evidence_rows
                )
                revisions = session.scalars(
                    select(ExtractionRevision).where(
                        ExtractionRevision.source_version_id == version.source_version_id
                    )
                ).all()
                for revision in revisions:
                    section_rows = session.scalars(
                        select(PostingSection)
                        .where(
                            PostingSection.extraction_revision_id == revision.extraction_revision_id
                        )
                        .order_by(PostingSection.order, PostingSection.section_key)
                    ).all()
                    sections = tuple(self._section_value(session, row) for row in section_rows)
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

    def poll_job(self, *, owner_user_id: UUID, job_id: UUID) -> dict[str, object] | None:
        if self._job_owners.get(job_id) != owner_user_id:
            return None
        result = self._results[job_id]
        failure = result.failures[0] if result.failures else None
        return {
            "job_id": str(job_id),
            "source_id": str(result.source_id),
            "status": "COMPLETE" if not result.failures else "FAILED",
            "input_version": result.input_version,
            "result_version": result.result_version,
            "resume_stage": result.resume_stage.value if result.resume_stage is not None else None,
            "failure": (
                {"stage": failure.stage.value, "code": failure.code}
                if failure is not None
                else None
            ),
        }

    def _section_value(self, session: Session, row: PostingSection) -> PostingSectionValue:
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

    def _dispatch(
        self,
        command: CollectionCommand,
        *,
        checkpoint_ref: str | None,
        retry_not_before: datetime | None,
    ) -> None:
        policy = self._policy_by_source[command.source_id]
        input_value = StaticCollectionInput(
            source_id=command.source_id,
            company_id=command.company_id,
            source_url=SOURCE_URL,
            title="Synthetic static posting",
            source_type=SourceType.JOB_POSTING,
            policy=policy,
            policy_revision=1,
            robots_permission=Permission.ALLOWED,
            limits=_limits(),
            result_version=command.input_version,
            aggregate_revision=command.input_version,
            language=cast(str, ANNOTATIONS["language"]),
        )
        permit = WorkerExecutionPermit(
            attempt_id=uuid4(),
            command=command,
            checkpoint_ref=checkpoint_ref,
            all_core_decisions_received=True,
            slot_acquired=True,
            retry_not_before=retry_not_before,
        )
        control = _WorkerControl(permit)
        worker = SourceCollectionWorker(
            control=control,
            execution_factory=lambda issued: StaticCollectionExecution(
                attempt_id=issued.attempt_id,
                input_provider=_InputProvider(input_value),
                collector=self.collector,
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
        self._results[command.job_id] = result
        self.stage_histories.append(tuple(control.stages))

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


def _request_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _app(assembly: _PostingAssembly) -> FastAPI:
    service = JobPostingService(repository=assembly, job_port=assembly)
    app = create_job_posting_api(
        service=service,
        authenticator=_authenticate,
        settings=SETTINGS,
    )

    @app.get("/api/v1/jobs/{job_id}")
    def get_job_status(job_id: UUID, request: Request) -> dict[str, object]:
        principal = _authenticate(request)
        if principal is None:
            raise ApiProblem(ApiErrorCode.AUTHENTICATION_REQUIRED)
        result = assembly.poll_job(owner_user_id=principal.user_id, job_id=job_id)
        if result is None:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        return result

    return app


def _recursive_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_recursive_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_recursive_keys(item))
    return keys


def _import(app: FastAPI, key: str) -> _AsgiResponse:
    return _request(
        app,
        "POST",
        "/api/v1/job-postings/import",
        headers=_headers(key),
        body={
            "company_id": str(COMPANY_ID),
            "url": SOURCE_URL,
            "analysis_request_id": "analysis-t036",
        },
    )


def test_import_persists_original_sections_evidence_and_request_deduplication(
    session_factory: sessionmaker[Session],
) -> None:
    assembly = _PostingAssembly(session_factory, outcomes=[None])
    app = _app(assembly)

    first = _import(app, "posting-import-success")
    replay = _import(app, "posting-import-success")

    assert first.status_code == replay.status_code == 202
    assert replay.payload() == first.payload()
    assert assembly.dispatches == 1
    accepted = first.payload()
    job_id = UUID(accepted["job_id"])
    posting_id = UUID(accepted["job_posting_id"])

    status = _request(app, "GET", accepted["status_url"], headers=_headers())
    assert status.status_code == 200
    assert status.payload()["status"] == "COMPLETE"

    response = _request(
        app,
        "GET",
        f"/api/v1/job-postings/{posting_id}",
        headers=_headers(),
    )
    assert response.status_code == 200
    public_posting = cast(dict[str, Any], response.payload()["job_posting"])
    forbidden = {
        "claim",
        "claims",
        "requirement_satisfaction",
        "eligibility",
        "importance",
        "ranking",
        "recommendation",
        "relation_text",
        "raw_body",
        "authenticated_owner_ref",
        "purpose_ref",
    }
    assert forbidden.isdisjoint(_recursive_keys(public_posting))

    expected = cast(dict[str, Any], ANNOTATIONS["expected"])
    expected_sections = cast(list[dict[str, Any]], expected["posting_sections"])
    expected_evidence = {
        item["ref"]: item for item in cast(list[dict[str, Any]], expected["evidence"])
    }
    public_sections = cast(list[dict[str, Any]], public_posting["sections"])
    assert len(public_sections) == len(expected_sections) == 11
    for actual, annotated in zip(public_sections, expected_sections, strict=True):
        assert set(actual) == {"kind", "text", "evidence"}
        assert actual["kind"] == annotated["kind"]
        assert actual["text"] == annotated["text_raw"]
        assert [item["excerpt"] for item in actual["evidence"]] == [
            expected_evidence[ref]["text_excerpt"] for ref in annotated["evidence_refs"]
        ]

    with session_factory() as session:
        assert _count(session, JobPosting) == 1
        assert _count(session, SourceVersion) == 1
        assert _count(session, ExtractionRevision) == 1
        assert _count(session, Evidence) == 15
        assert _count(session, PostingSection) == 11
        assert _count(session, CollectionAttempt) == 1
        assert _count(session, OutboxEvent) == 1
        assert _count(session, RequestDeduplication) == 1
        source = session.get(Source, UUID(accepted["source_id"]))
        assert source is not None
        assert source.current_source_version_id == UUID(public_posting["source_version_id"])

        evidence_rows = session.scalars(
            select(Evidence).order_by(Evidence.chunk_order, Evidence.evidence_id)
        ).all()
        annotation_ref_by_evidence_id: dict[UUID, str] = {}
        for actual, annotated in zip(
            evidence_rows,
            cast(list[dict[str, Any]], expected["evidence"]),
            strict=True,
        ):
            assert actual.evidence_key.startswith("evidence:")
            assert actual.section_title == annotated["section_heading"]
            assert actual.text_excerpt == annotated["text_excerpt"]
            assert actual.chunk_order == annotated["chunk_order"]
            assert actual.locator["kind"] == "xpath"
            assert actual.locator["value"] == annotated["xpath"]
            annotation_ref_by_evidence_id[actual.evidence_id] = annotated["ref"]

        section_rows = session.scalars(select(PostingSection).order_by(PostingSection.order)).all()
        sections_by_fixture_id: dict[str, PostingSection] = {}
        for actual, annotated in zip(section_rows, expected_sections, strict=True):
            assert actual.section_key.startswith("posting_section:")
            assert actual.kind == annotated["kind"]
            assert actual.heading_raw == annotated.get("heading_raw")
            assert actual.text_raw == annotated["text_raw"]
            assert actual.order == annotated["order"]
            assert actual.relation_text == annotated.get("relation_text")
            sections_by_fixture_id[annotated["section_id"]] = actual
            links = session.scalars(
                select(PostingSectionEvidence.evidence_id)
                .where(
                    PostingSectionEvidence.extraction_revision_id == actual.extraction_revision_id,
                    PostingSectionEvidence.section_key == actual.section_key,
                )
                .order_by(PostingSectionEvidence.evidence_order)
            ).all()
            assert [annotation_ref_by_evidence_id[evidence_id] for evidence_id in links] == (
                annotated["evidence_refs"]
            )

        assert sections_by_fixture_id["gcp_general_context"].kind == "general"
        assert not any(row.kind == "required" and "GCP" in row.text_raw for row in section_rows)
        assert sections_by_fixture_id["python_and_sql_required"].relation_text == (
            "You must demonstrate Python and SQL experience."
        )
        assert sections_by_fixture_id["kubernetes_or_docker_required"].relation_text == (
            "You must demonstrate Kubernetes or Docker experience."
        )
        assert sections_by_fixture_id["same_experience_relation"].relation_text == (
            "The same experience may satisfy the AWS requirement and the Python and SQL "
            "requirement."
        )
        attempt = session.scalars(select(CollectionAttempt)).one()
        assert attempt.job_id == job_id
        assert attempt.failures == []


def test_w1_issued_retry_resumes_persisted_failed_stage_without_w2_retry_route(
    session_factory: sessionmaker[Session],
) -> None:
    assembly = _PostingAssembly(
        session_factory,
        outcomes=[StaticFetchFailureCode.RATE_LIMITED, None],
    )
    app = _app(assembly)
    imported = _import(app, "posting-import-rate-limited")
    assert imported.status_code == 202
    accepted = imported.payload()
    posting_id = UUID(accepted["job_posting_id"])

    failed = _request(app, "GET", accepted["status_url"], headers=_headers())
    assert failed.status_code == 200
    assert failed.payload()["status"] == "FAILED"
    assert failed.payload()["failure"] == {"stage": "fetch", "code": "RATE_LIMITED"}
    assert failed.payload()["resume_stage"] == "fetch"

    # W2 must not accept user retry actions or dispatch from its old posting route.
    rejected_retry = _request(
        app,
        "POST",
        f"/api/v1/job-postings/{posting_id}/retry",
        headers=_headers("posting-retry-rate-limited"),
        body={
            "job_id": accepted["job_id"],
            "expected_input_version": failed.payload()["input_version"],
            "expected_result_version": failed.payload()["result_version"],
        },
    )
    assert rejected_retry.status_code == 404
    assert assembly.dispatches == 1
    assert len(assembly.collector.requests) == 1
    with session_factory() as session:
        assert _count(session, CollectionAttempt) == 1
        assert _count(session, RequestDeduplication) == 1

    # This test-local W1 owner supplies the resumed command directly. It does not
    # implement or verify W1's public Job action API, slot policy or real dispatch.
    for _ in range(2):
        assembly.retry_job_posting(
            owner_user_id=OWNER_ID,
            job_posting_id=posting_id,
            job_id=UUID(accepted["job_id"]),
            expected_input_version=failed.payload()["input_version"],
            expected_result_version=failed.payload()["result_version"],
            idempotency_key="posting-retry-rate-limited",
        )
    assert assembly.dispatches == 2
    assert len(assembly.collector.requests) == 2
    assert assembly.stage_histories[1][0] is CollectionStage.FETCH
    assert CollectionStage.POLICY not in assembly.stage_histories[1]

    completed = _request(app, "GET", accepted["status_url"], headers=_headers())
    assert completed.status_code == 200
    assert completed.payload()["status"] == "COMPLETE"
    assert completed.payload()["input_version"] == 2
    assert completed.payload()["result_version"] == 2
    posting = _request(
        app,
        "GET",
        f"/api/v1/job-postings/{posting_id}",
        headers=_headers(),
    )
    assert posting.status_code == 200
    assert len(posting.payload()["job_posting"]["sections"]) == 11

    with session_factory() as session:
        assert _count(session, JobPosting) == 1
        assert _count(session, CollectionAttempt) == 2
        assert _count(session, SourceVersion) == 1
        assert _count(session, ExtractionRevision) == 1
        assert _count(session, RequestDeduplication) == 2
        assert _count(session, OutboxEvent) == 2
        attempts = session.scalars(
            select(CollectionAttempt).order_by(CollectionAttempt.input_version)
        ).all()
        assert [attempt.resume_stage for attempt in attempts] == ["policy", "fetch"]
        assert attempts[0].failures[0]["stage"] == "fetch"
        assert attempts[0].failures[0]["code"] == "RATE_LIMITED"
        assert attempts[0].checkpoint_ref is not None
        assert attempts[1].failures == []
        assert attempts[1].checkpoint_ref is None


def _count(session: Session, model: type[Base]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)
