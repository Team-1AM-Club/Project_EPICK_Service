"""T027 source-registration boundary assembly.

The transport below is deliberately in-process: these tests exercise the public
API plus the real W2 worker and static pipeline without opening a socket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from starlette.types import Message, Receive, Scope, Send

import epick_engine.source_collection.collector as collector_module
from epick_engine.source_collection.api import (
    ApiBoundarySettings,
    ApiErrorCode,
    ApiProblem,
    AuthenticatedPrincipal,
    CursorCodec,
    create_company_source_api,
)
from epick_engine.source_collection.collector import (
    StaticFetchRequest,
    StaticFetchResult,
    StaticResponseCandidate,
    StaticResponseSnapshot,
)
from epick_engine.source_collection.contracts import (
    AccessClass,
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    CompletionKind,
    CoreSourceDecision,
    OfficialStatus,
    Permission,
    Policy,
    SourceType,
)
from epick_engine.source_collection.parsing import extract_static_candidate
from epick_engine.source_collection.persistence import PreparedCollectionCommit
from epick_engine.source_collection.policy import (
    ExecutionLimits,
    Representation,
    UntrustedDocument,
    ValidatedTarget,
)
from epick_engine.source_collection.service import (
    CompanyNotFound,
    StaticCollectionExecution,
    StaticCollectionInput,
)
from epick_engine.source_collection.worker import (
    SourceCollectionWorker,
    WorkerExecutionPermit,
)

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
OWNER_ID = UUID("00000000-0000-4000-8000-000000000501")
COMPANY_ID = UUID("00000000-0000-4000-8000-000000000502")
OTHER_COMPANY_ID = UUID("00000000-0000-4000-8000-000000000503")
HTML = "<html><main><h2>채용 요건</h2><p>Python 경험</p></main></html>"
_API_EVENT_LOOP: asyncio.AbstractEventLoop | None = None


@pytest.fixture(scope="module", autouse=True)
def _manage_api_event_loop() -> Iterator[None]:
    global _API_EVENT_LOOP
    event_loop = asyncio.new_event_loop()
    _API_EVENT_LOOP = event_loop
    try:
        yield
    finally:
        event_loop.close()
        _API_EVENT_LOOP = None


def _complete(awaitable: Awaitable[Any]) -> Any:
    if _API_EVENT_LOOP is None:
        raise RuntimeError("test-local API event loop is not configured")
    return _API_EVENT_LOOP.run_until_complete(cast(Any, awaitable))


class _NoNetworkCollector:
    """An isolated transport seam that never opens a socket."""

    def __init__(self, result: StaticFetchResult, *, pdf_redirect: bool = False) -> None:
        self.result = result
        self.pdf_redirect = pdf_redirect
        self.requests: list[StaticFetchRequest] = []

    def fetch(self, request: StaticFetchRequest) -> StaticFetchResult:
        self.requests.append(request)
        assert request.source_url.startswith("https://")
        if self.pdf_redirect:
            initial_target = ValidatedTarget(
                url=request.source_url,
                hostname="careers.example.test",
                port=443,
                resolved_addresses=frozenset({"1.1.1.1"}),
            )
            redirect = collector_module._evaluate_static_response(
                request,
                StaticResponseSnapshot(
                    url=request.source_url,
                    status_code=302,
                    headers={"Location": "/jobs/redirect.pdf"},
                    body=b"",
                    peer_address="1.1.1.1",
                    raw_size=0,
                ),
                initial_target,
                0,
                lambda _hostname: ("1.1.1.1",),
            )
            assert isinstance(redirect, collector_module._StaticRedirect)
            evaluated = collector_module._evaluate_static_response(
                request,
                StaticResponseSnapshot(
                    url=redirect.target.url,
                    status_code=200,
                    headers={"Content-Type": "application/pdf"},
                    body=b"%PDF-1.7 isolated redirect",
                    peer_address="1.1.1.1",
                    raw_size=len(b"%PDF-1.7 isolated redirect"),
                ),
                redirect.target,
                redirect.redirect_hops,
                lambda _hostname: ("1.1.1.1",),
            )
            assert isinstance(evaluated, StaticFetchResult)
            return evaluated
        return self.result


class _W1Harness:
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
        raise AssertionError("the isolated worker must complete through its finalizer")


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


def _policy(*, collection: Permission = Permission.ALLOWED) -> Policy:
    return Policy(
        policy_decision_id=uuid4(),
        official_status=OfficialStatus.VERIFIED,
        access_class=AccessClass.PUBLIC,
        collection_permission=collection,
        excerpt_storage_permission=Permission.ALLOWED,
        body_storage_permission=Permission.DENIED,
        redistribution_permission=Permission.DENIED,
        checked_at=NOW,
        policy_version="integration.policy.v1",
    )


def _command(source_id: UUID, job_id: UUID) -> CollectionCommand:
    return CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=uuid4(),
        job_id=job_id,
        authenticated_owner_ref=OWNER_ID,
        project_ref="integration-project",
        company_id=COMPANY_ID,
        source_id=source_id,
        input_version=1,
        execution_fence="integration-fence",
        purpose_ref=uuid4(),
        core_source_decision=CoreSourceDecision(
            is_core=True,
            decided_by="integration-test",
            rationale="official source",
            decision_revision=1,
            analysis_input_version=1,
        ),
        resume_stage=CollectionStage.POLICY,
        policy_revision=None,
        owner_deletion_epoch=0,
    )


def _candidate(url: str) -> StaticResponseCandidate:
    return StaticResponseCandidate(
        final_target=ValidatedTarget(
            url=url,
            hostname="careers.example.test",
            port=443,
            resolved_addresses=frozenset({"198.51.100.20"}),
        ),
        representation=Representation.HTML,
        document=UntrustedDocument(HTML),
        http_status=200,
        raw_size=len(HTML.encode()),
        decompressed_size=len(HTML.encode()),
    )


def _run_pipeline(
    *,
    source_id: UUID,
    job_id: UUID,
    url: str,
    policy: Policy,
    pdf_redirect: bool = False,
) -> tuple[CollectionResult, _NoNetworkCollector, _W1Harness, PreparedCollectionCommit]:
    command = _command(source_id, job_id)
    collector = _NoNetworkCollector(
        StaticFetchResult(
            command_id=command.command_id,
            candidate=_candidate(url),
            failure_code=None,
        ),
        pdf_redirect=pdf_redirect,
    )
    input_value = StaticCollectionInput(
        source_id=source_id,
        company_id=COMPANY_ID,
        source_url=url,
        title="통합 채용 공고",
        source_type=SourceType.JOB_POSTING,
        policy=policy,
        policy_revision=1,
        robots_permission=Permission.ALLOWED,
        limits=_limits(),
        result_version=1,
        aggregate_revision=1,
        language="ko",
        redirect_robots_permissions=(
            ("https://careers.example.test/jobs/redirect.pdf", Permission.ALLOWED),
        )
        if pdf_redirect
        else (),
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
    commits: list[object] = []

    def commit(
        _session_factory: object,
        *,
        prepared: Any,
        **_kwargs: object,
    ) -> CollectionResult:
        commits.append(prepared)
        return prepared.result

    worker = SourceCollectionWorker(
        control=control,
        execution_factory=lambda permit: StaticCollectionExecution(
            attempt_id=permit.attempt_id,
            input_provider=type("Input", (), {"load": lambda _self, _command: input_value})(),
            collector=collector,
            parser=extract_static_candidate,
            clock=lambda: NOW,
            uuid_factory=uuid4,
        ),
        session_factory=lambda: None,
        lock_authority=lambda *_args, **_kwargs: None,
        replayer=lambda *_args, **_kwargs: None,
        committer=commit,
        clock=lambda: NOW,
    )
    result = worker.handle(command.model_dump(mode="json"))
    assert len(commits) == 1
    assert control.finalized is result
    return result, collector, control, cast(PreparedCollectionCommit, commits[0])


class _RegistrationHarness:
    def __init__(self, *, policy: Policy, pdf_redirect: bool = False) -> None:
        self.policy = policy
        self.pdf_redirect = pdf_redirect
        self.records: dict[tuple[UUID, UUID, str], tuple[dict[str, object], dict[str, str]]] = {}
        self.dispatches = 0
        self.collectors: list[_NoNetworkCollector] = []
        self.results: dict[str, CollectionResult] = {}
        self.prepared: dict[str, PreparedCollectionCommit] = {}
        self.source_companies: dict[str, UUID] = {}
        self.controls: dict[str, _W1Harness] = {}
        self.job_owners: dict[str, UUID] = {}

    def register_source(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        source_type: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> dict[str, str]:
        assert owner_user_id == OWNER_ID
        if company_id != COMPANY_ID:
            raise CompanyNotFound("the selected company does not own this source")
        payload = {
            "url": url,
            "source_type": source_type,
            "analysis_request_id": analysis_request_id,
        }
        key = (owner_user_id, company_id, idempotency_key)
        if existing := self.records.get(key):
            if existing[0] != payload:
                raise ApiProblem(ApiErrorCode.IDEMPOTENCY_CONFLICT)
            return existing[1]
        source_id, job_id = uuid4(), uuid4()
        self.dispatches += 1
        result, collector, control, prepared = _run_pipeline(
            source_id=source_id,
            job_id=job_id,
            url=url,
            policy=self.policy,
            pdf_redirect=self.pdf_redirect,
        )
        response = {
            "source_id": str(source_id),
            "job_id": str(job_id),
            "status_url": f"/api/v1/jobs/{job_id}",
        }
        self.records[key] = (payload, response)
        self.collectors.append(collector)
        self.results[str(job_id)] = result
        self.prepared[str(job_id)] = prepared
        self.source_companies[str(source_id)] = company_id
        self.controls[str(job_id)] = control
        self.job_owners[str(job_id)] = owner_user_id
        return response

    def source_ids_for_company(self, company_id: UUID) -> list[str]:
        return [
            source_id for source_id, owner in self.source_companies.items() if owner == company_id
        ]

    def poll_job(self, *, owner_user_id: UUID, job_id: UUID) -> dict[str, object] | None:
        job_key = str(job_id)
        if self.job_owners.get(job_key) != owner_user_id:
            return None
        result = self.results[job_key]
        if result.completion_kind is CompletionKind.COMPLETE:
            return {
                "job_id": job_key,
                "source_id": str(result.source_id),
                "status": "COMPLETE",
                "failure": None,
                "restriction": None,
                "actions": [],
            }
        failure = result.failures[0]
        return {
            "job_id": job_key,
            "source_id": str(result.source_id),
            "status": "FAILED_FINAL",
            "failure": {"stage": failure.stage.value, "code": failure.code},
            "restriction": {"code": failure.code, "impact_ko": failure.impact},
            "actions": [
                {"code": action.code, "label_ko": action.label_ko}
                for action in result.required_actions
            ],
        }


def _app(service: _RegistrationHarness) -> FastAPI:
    settings = ApiBoundarySettings(
        idempotency_key_max_length=256,
        cursor_max_token_length=1024,
        page_default_limit=20,
        page_max_limit=100,
    )
    app = create_company_source_api(
        service=service,
        authenticator=lambda _request: AuthenticatedPrincipal(OWNER_ID),
        settings=settings,
        cursor_codec=CursorCodec("x" * 32, settings),
    )

    @app.get("/api/v1/jobs/{job_id}")
    def poll_job(job_id: UUID) -> dict[str, object]:
        result = service.poll_job(owner_user_id=OWNER_ID, job_id=job_id)
        if result is None:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        return result

    return app


def _post(
    app: FastAPI,
    *,
    key: str,
    body: dict[str, object],
    company_id: UUID = COMPANY_ID,
) -> tuple[int, dict[str, object]]:
    payload = json.dumps(body).encode()
    messages: list[Message] = []
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": f"/api/v1/companies/{company_id}/sources",
            "raw_path": f"/api/v1/companies/{company_id}/sources".encode(),
            "query_string": b"",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
                (b"idempotency-key", key.encode()),
            ],
            "client": ("test-client", 1),
            "server": ("testserver", 80),
        },
    )
    _complete(app(scope, cast(Receive, receive), cast(Send, send)))
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw = b"".join(
        cast(bytes, message.get("body", b""))
        for message in messages
        if message["type"] == "http.response.body"
    )
    return cast(int, start["status"]), cast(dict[str, object], json.loads(raw))


def _get(app: FastAPI, path: str) -> tuple[int, dict[str, object]]:
    messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        messages.append(message)

    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"host", b"testserver")],
            "client": ("test-client", 1),
            "server": ("testserver", 80),
        },
    )
    _complete(app(scope, cast(Receive, receive), cast(Send, send)))
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw = b"".join(
        cast(bytes, message.get("body", b""))
        for message in messages
        if message["type"] == "http.response.body"
    )
    return cast(int, start["status"]), cast(dict[str, object], json.loads(raw))


def test_direct_html_registration_accepts_once_dispatches_once_and_completes() -> None:
    service = _RegistrationHarness(policy=_policy())
    app = _app(service)
    status, accepted = _post(
        app,
        key="html-1",
        body={"url": "https://careers.example.test/jobs/1", "source_type": "job_posting"},
    )
    dispatches_before_poll = service.dispatches
    fetches_before_poll = len(service.collectors[0].requests)
    poll_status, polled = _get(app, cast(str, accepted["status_url"]))

    assert status == 202 and poll_status == 200
    assert polled == {
        "job_id": accepted["job_id"],
        "source_id": accepted["source_id"],
        "status": "COMPLETE",
        "failure": None,
        "restriction": None,
        "actions": [],
    }
    assert "prepared" not in json.dumps(polled)
    assert service.dispatches == dispatches_before_poll == 1
    assert len(service.collectors[0].requests) == fetches_before_poll == 1
    job_id = cast(str, accepted["job_id"])
    control = service.controls[job_id]
    prepared = service.prepared[job_id]
    assert control.permit.command.job_id == UUID(job_id)
    assert control.finalized is not None and control.finalized.job_id == UUID(job_id)
    assert prepared.result.source_id == UUID(cast(str, accepted["source_id"]))
    assert prepared.source_version is not None
    assert prepared.source_version.source_id == prepared.result.source_id
    assert prepared.observation is not None
    assert prepared.evidence and all(
        evidence.source_version_id == prepared.source_version.source_version_id
        for evidence in prepared.evidence
    )


def test_pdf_redirect_failure_is_final_without_version_or_evidence() -> None:
    service = _RegistrationHarness(policy=_policy(), pdf_redirect=True)
    app = _app(service)
    status, accepted = _post(
        app,
        key="pdf-1",
        body={
            "url": "https://careers.example.test/jobs/initial",
            "source_type": "job_posting",
        },
    )
    fetches_before_poll = len(service.collectors[0].requests)
    poll_status, polled = _get(app, cast(str, accepted["status_url"]))

    assert status == 202 and poll_status == 200
    assert polled["status"] == "FAILED_FINAL"
    assert polled["source_id"] == accepted["source_id"]
    assert polled["failure"] == {"stage": "fetch", "code": "UNSUPPORTED_FORMAT"}
    assert polled["restriction"] == {
        "code": "UNSUPPORTED_FORMAT",
        "impact_ko": "소스 응답을 안전하게 수집하지 못했습니다.",
    }
    assert polled["actions"] == [
        {"code": "core_failure_decision", "label_ko": "핵심 소스의 처리 방식을 선택해 주세요."},
        {"code": "find_alternative_source", "label_ko": "대체 가능한 공식 소스를 찾아 주세요."},
    ]
    assert "prepared" not in json.dumps(polled)
    assert len(service.collectors[0].requests) == fetches_before_poll == 1
    prepared = service.prepared[cast(str, accepted["job_id"])]
    assert prepared.source_version is None and prepared.observation is not None
    assert prepared.evidence == ()


@pytest.mark.parametrize("permission", [Permission.UNKNOWN, Permission.DENIED])
def test_policy_non_allowance_does_not_fetch_and_returns_korean_action(
    permission: Permission,
) -> None:
    service = _RegistrationHarness(policy=_policy(collection=permission))
    app = _app(service)
    status, accepted = _post(
        app,
        key=f"policy-{permission.value}",
        body={"url": "https://careers.example.test/jobs/2", "source_type": "job_posting"},
    )
    poll_status, polled = _get(app, cast(str, accepted["status_url"]))

    assert status == 202 and poll_status == 200
    assert polled["status"] == "FAILED_FINAL"
    assert polled["failure"] == {"stage": "policy", "code": "SOURCE_POLICY_BLOCKED"}
    assert polled["restriction"] == {
        "code": "SOURCE_POLICY_BLOCKED",
        "impact_ko": "정책상 수집 또는 발췌 저장이 허용되지 않았습니다.",
    }
    assert polled["actions"] == [
        {"code": "core_failure_decision", "label_ko": "핵심 소스의 처리 방식을 선택해 주세요."},
        {"code": "find_alternative_source", "label_ko": "대체 가능한 공식 소스를 찾아 주세요."},
    ]
    assert "prepared" not in json.dumps(polled)
    assert service.dispatches == 1
    assert service.collectors[0].requests == []


def test_same_idempotency_key_replays_and_changed_payload_conflicts_without_dispatch() -> None:
    service = _RegistrationHarness(policy=_policy())
    app = _app(service)
    first = _post(
        app,
        key="replay-1",
        body={"url": "https://careers.example.test/jobs/3", "source_type": "job_posting"},
    )
    replay = _post(
        app,
        key="replay-1",
        body={"url": "https://careers.example.test/jobs/3", "source_type": "job_posting"},
    )
    conflict = _post(
        app,
        key="replay-1",
        body={"url": "https://careers.example.test/jobs/3", "source_type": "company_website"},
    )

    assert first[0] == replay[0] == 202 and first[1] == replay[1]
    assert conflict[0] == 409
    assert (
        cast(dict[str, object], conflict[1]["error"])["code"] == ApiErrorCode.IDEMPOTENCY_CONFLICT
    )
    assert service.dispatches == len(service.collectors) == 1


def test_source_is_bound_to_selected_company_and_never_dispatches_for_another_company() -> None:
    service = _RegistrationHarness(policy=_policy())
    app = _app(service)
    body = {"url": "https://careers.example.test/jobs/shared", "source_type": "job_posting"}
    accepted_status, accepted = _post(app, key="company-a", body=body)
    rejected_status, _rejected = _post(
        app,
        key="company-b",
        body=body,
        company_id=OTHER_COMPANY_ID,
    )

    assert accepted_status == 202 and rejected_status == 404
    assert service.source_companies[cast(str, accepted["source_id"])] == COMPANY_ID
    assert service.source_ids_for_company(OTHER_COMPANY_ID) == []
    assert service.dispatches == len(service.collectors) == 1
    assert len(service.collectors[0].requests) == 1
