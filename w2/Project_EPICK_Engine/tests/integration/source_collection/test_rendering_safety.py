"""T048 rendering-safety acceptance coverage.

Rendered collection has one production seam: ``RenderedCollector.fetch``.
Browser-facing tests inject an event-only runtime; it supplies browser protocol
data but never manufactures collection results. This keeps the checks
executable without a browser binary, network access, or a test-local renderer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

import epick_engine.source_collection.collector as collector_module
from epick_engine.source_collection.collector import (
    StaticFetchFailureCode,
    StaticFetchRequest,
    StaticFetchResult,
    StaticResponseSnapshot,
    StaticScrapyCollector,
    _collector_settings,
    _evaluate_static_response,
)
from epick_engine.source_collection.contracts import ExtractionStatus
from epick_engine.source_collection.parsing import extract_static_candidate
from epick_engine.source_collection.policy import (
    AccessClass,
    ExecutionLimits,
    OfficialStatus,
    Permission,
    PolicySnapshot,
    Representation,
    validate_url,
)

_COMMAND_ID = UUID("00000000-0000-4000-8000-000000004801")
_PUBLIC_IP = "8.8.8.8"
_SOURCE_URL = "https://synthetic-rendering.test/careers"
_REDIRECT_URL = "https://synthetic-rendering.test/careers/rendered"
_SUBREQUEST_URL = "https://assets.synthetic-rendering.test/app.js"
_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic_sources"
_RENDERED_HTML = (
    '<main id="synthetic-render-root"><section><h1>Rendered synthetic company update</h1>'
    "<p>This paragraph exists only after synchronous synthetic rendering.</p>"
    "</section></main>"
)
_REAL_BROWSER_GATE_REASON = (
    "T048 real-browser validation is incomplete: an approved Playwright package, "
    "browser executable, "
    "and external-egress gate are required."
)


def _resolver(*addresses: str):
    def resolve(_hostname: str) -> Iterable[str]:
        return addresses

    return resolve


def _recording_resolver(calls: list[str]):
    def resolve(hostname: str) -> Iterable[str]:
        calls.append(hostname)
        return (_PUBLIC_IP,)

    return resolve


def _request(command_id: UUID = _COMMAND_ID) -> StaticFetchRequest:
    return StaticFetchRequest(
        command_id=command_id,
        source_url=_SOURCE_URL,
        policy=PolicySnapshot(
            official_status=OfficialStatus.VERIFIED,
            access_class=AccessClass.PUBLIC,
            collection_permission=Permission.ALLOWED,
            excerpt_storage_permission=Permission.ALLOWED,
            body_storage_permission=Permission.DENIED,
            redistribution_permission=Permission.DENIED,
            revision=1,
        ),
        robots_permission=Permission.ALLOWED,
        limits=ExecutionLimits(
            site_concurrency=1,
            global_concurrency=1,
            source_ttl_seconds=3600,
            max_response_bytes=128_000,
            max_decompressed_bytes=128_000,
            connect_timeout_seconds=1.0,
            read_timeout_seconds=1.0,
            max_redirects=1,
            general_retry_limit=1,
            retention_days=30,
        ),
    )


def _snapshot(
    *, body: bytes, status_code: int = 200, headers: dict[str, str] | None = None
) -> StaticResponseSnapshot:
    response_headers = {"Content-Type": "text/html; charset=utf-8"}
    response_headers.update(headers or {})
    return StaticResponseSnapshot(
        url=_SOURCE_URL,
        status_code=status_code,
        headers=response_headers,
        body=body,
        peer_address=_PUBLIC_IP,
        raw_size=len(body),
    )


class _StaticLedgerConnection:
    def __init__(self, events: list[str], values: list[object] | None = None) -> None:
        self._events = events
        self._values = list(values or ())
        self.sent: list[object] = []

    def close(self) -> None:
        return None

    def poll(self, _timeout: float = 0.0) -> bool:
        return bool(self._values)

    def recv(self) -> object:
        return self._values.pop(0)

    def send(self, value: object) -> None:
        self._events.append("static.request.send")
        self.sent.append(value)


class _StaticProcess:
    def __init__(self, events: list[str], *, stubborn: bool) -> None:
        self._events = events
        self._stubborn = stubborn
        self._alive = False

    def start(self) -> None:
        self._events.append("static.process.start")
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, _timeout: float | None = None) -> None:
        self._events.append("static.process.join")
        if not self._stubborn:
            self._alive = False

    def terminate(self) -> None:
        self._events.append("static.process.terminate")
        if not self._stubborn:
            self._alive = False

    def kill(self) -> None:
        self._events.append("static.process.kill")
        self._alive = False


class _StaticProcessContext:
    def __init__(
        self,
        result_values: list[object] | None = None,
        *,
        stubborn: bool = False,
    ) -> None:
        self.events: list[str] = []
        self.parent_inputs: list[_StaticLedgerConnection] = []
        self.processes: list[_StaticProcess] = []
        self._result_values = list(result_values or ())
        self._stubborn = stubborn
        self._pipe_count = 0

    def Pipe(self, duplex: bool = True) -> tuple[_StaticLedgerConnection, _StaticLedgerConnection]:
        assert duplex is False
        self._pipe_count += 1
        if self._pipe_count % 2:
            child_input = _StaticLedgerConnection(self.events)
            parent_input = _StaticLedgerConnection(self.events)
            self.parent_inputs.append(parent_input)
            return child_input, parent_input
        parent_output = _StaticLedgerConnection(self.events, self._result_values)
        child_output = _StaticLedgerConnection(self.events)
        self._result_values = []
        return parent_output, child_output

    def Process(
        self,
        *,
        target: object,
        args: tuple[object, ...],
        daemon: bool,
    ) -> _StaticProcess:
        assert target is collector_module._run_static_child
        assert len(args) == 2
        assert daemon is False
        process = _StaticProcess(self.events, stubborn=self._stubborn)
        self.processes.append(process)
        return process


@dataclass(frozen=True)
class _BrowserRequest:
    url: str
    peer_address: str | None
    resource_type: str


@dataclass(frozen=True)
class _BrowserResponse:
    url: str
    status: int
    headers: dict[str, str]
    peer_address: str | None


class _RouteBlocked(RuntimeError):
    pass


class _BrowserRoute:
    def __init__(self, request: _BrowserRequest, events: list[str]) -> None:
        self.request = request
        self._events = events
        self.continued = False
        self.aborted = False

    def continue_(self) -> None:
        self._events.append(f"route.continue:{self.request.url}")
        self.continued = True

    def abort(self) -> None:
        self._events.append(f"route.abort:{self.request.url}")
        self.aborted = True


class _BrowserChild:
    def __init__(self, events: list[str], *, stubborn: bool) -> None:
        self._events = events
        self._stubborn = stubborn
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def terminate(self) -> None:
        self._events.append("child.terminate")
        if not self._stubborn:
            self._alive = False

    def join(self) -> None:
        self._events.append("child.join")

    def kill(self) -> None:
        self._events.append("child.kill")
        self._alive = False


class _BrowserPage:
    def __init__(self, runtime: _BrowserRuntime) -> None:
        self._runtime = runtime
        self._route_handler: Callable[[_BrowserRoute], None] | None = None

    def route(self, pattern: str, handler: Callable[[_BrowserRoute], None]) -> None:
        self._runtime.events.append(f"page.route:{pattern}")
        self._route_handler = handler

    def goto(self, url: str) -> _BrowserResponse:
        assert self._route_handler is not None
        self._runtime.events.append(f"page.goto:{url}")
        for request in self._runtime.requests:
            route = _BrowserRoute(request, self._runtime.events)
            self._runtime.routed_urls.append(request.url)
            self._route_handler(route)
            if route.aborted:
                raise _RouteBlocked(request.url)
            assert route.continued is True
        if self._runtime.on_goto is not None:
            self._runtime.on_goto()
        return self._runtime.response

    def content(self) -> str:
        self._runtime.events.append("page.content")
        return self._runtime.document

    def close(self) -> None:
        self._runtime.events.append("page.close")


class _BrowserContext:
    def __init__(self, runtime: _BrowserRuntime) -> None:
        self._runtime = runtime

    def new_page(self) -> _BrowserPage:
        self._runtime.events.append("context.new_page")
        return _BrowserPage(self._runtime)

    def storage_state(self) -> None:
        self._runtime.storage_state_reads += 1
        return None

    def cookies(self) -> tuple[object, ...]:
        self._runtime.cookie_reads += 1
        return ()

    def close(self) -> None:
        self._runtime.events.append("context.close")


class _Browser:
    def __init__(self, runtime: _BrowserRuntime) -> None:
        self._runtime = runtime

    def new_context(self, **options: object) -> _BrowserContext:
        self._runtime.context_options.append(options)
        self._runtime.events.append("browser.new_context")
        return _BrowserContext(self._runtime)

    def close(self) -> None:
        self._runtime.events.append("browser.close")


class _BrowserRuntime:
    def __init__(
        self,
        *,
        document: str,
        requests: list[_BrowserRequest],
        response: _BrowserResponse,
        stubborn_child: bool = False,
        on_goto: Callable[[], None] | None = None,
    ) -> None:
        self.document = document
        self.requests = requests
        self.response = response
        self.on_goto = on_goto
        self.events: list[str] = []
        self.routed_urls: list[str] = []
        self.context_options: list[dict[str, object]] = []
        self.storage_state_reads = 0
        self.cookie_reads = 0
        self.launches = 0
        self.child = _BrowserChild(self.events, stubborn=stubborn_child)

    def launch(self, *, headless: bool) -> _Browser:
        assert headless is True
        self.events.append("runtime.launch")
        self.launches += 1
        return _Browser(self)

    def reap(self, _browser: _Browser) -> None:
        self.events.append("runtime.reap")
        self.child.terminate()
        self.child.join()
        if self.child.is_alive():
            self.child.kill()
            self.child.join()


def _response(
    *, url: str = _REDIRECT_URL, status: int = 200, headers: dict[str, str] | None = None
) -> _BrowserResponse:
    response_headers = {"Content-Type": "text/html; charset=utf-8"}
    response_headers.update(headers or {})
    return _BrowserResponse(url, status, response_headers, _PUBLIC_IP)


def _runtime(
    *,
    document: str = _RENDERED_HTML,
    requests: list[_BrowserRequest] | None = None,
    response: _BrowserResponse | None = None,
    stubborn_child: bool = False,
    on_goto: Callable[[], None] | None = None,
) -> _BrowserRuntime:
    return _BrowserRuntime(
        document=document,
        requests=requests
        or [
            _BrowserRequest(_SOURCE_URL, _PUBLIC_IP, "document"),
            _BrowserRequest(_REDIRECT_URL, _PUBLIC_IP, "document"),
            _BrowserRequest(_SUBREQUEST_URL, _PUBLIC_IP, "script"),
        ],
        response=response or _response(),
        stubborn_child=stubborn_child,
        on_goto=on_goto,
    )


def _rendered_collector(
    runtime: _BrowserRuntime,
    resolver_calls: list[str],
    *,
    approved_js_urls: frozenset[str],
    resolver: Callable[[str], Iterable[str]] | None = None,
):
    renderer_type = collector_module.RenderedCollector
    return renderer_type(
        browser_runtime=runtime,
        resolver=resolver or _recording_resolver(resolver_calls),
        approved_js_urls=approved_js_urls,
    )


def _assert_in_order(events: list[str], expected: list[str]) -> None:
    offset = 0
    for event in expected:
        offset = events.index(event, offset) + 1


def test_js_shell_static_collection_has_no_script_derived_evidence() -> None:
    raw_html = (_FIXTURES / "js_shell.html").read_text(encoding="utf-8")
    request = _request()

    decision = _evaluate_static_response(
        request,
        _snapshot(body=raw_html.encode("utf-8")),
        validate_url(_SOURCE_URL, _resolver(_PUBLIC_IP)),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.candidate is not None
    parsed = extract_static_candidate(decision.candidate)
    assert parsed.extraction_status is ExtractionStatus.FAILED
    assert parsed.evidence == ()
    assert parsed.limitations == ("no_extractable_evidence",)
    assert all(
        primitive not in raw_html
        for primitive in (
            "fetch(",
            "XMLHttpRequest",
            "navigator.serviceWorker",
            "serviceWorker.register",
            "download=",
        )
    )


def test_static_document_remains_collectable_without_a_browser_runtime() -> None:
    request = _request()
    static_html = (_FIXTURES / "static_posting.html").read_bytes()

    decision = _evaluate_static_response(
        request,
        _snapshot(body=static_html),
        validate_url(_SOURCE_URL, _resolver(_PUBLIC_IP)),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is None
    assert decision.candidate is not None
    assert decision.candidate.document.text == static_html.decode("utf-8")


def test_static_rate_limit_handoff_has_one_child_dispatch_and_no_retry_configuration() -> None:
    request = _request()
    static_decision = _evaluate_static_response(
        request,
        _snapshot(body=b"", status_code=429, headers={"Retry-After": "120"}),
        validate_url(_SOURCE_URL, _resolver(_PUBLIC_IP)),
        0,
        _resolver(_PUBLIC_IP),
    )
    context = _StaticProcessContext([static_decision])

    result = StaticScrapyCollector(process_context=context, monotonic=lambda: 0.0).fetch(request)

    assert result is static_decision
    assert result.candidate is None
    assert result.failure_code is StaticFetchFailureCode.RATE_LIMITED
    assert result.retry_after == "120"
    assert [connection.sent for connection in context.parent_inputs] == [[request]]
    assert context.events[:3] == [
        "static.process.start",
        "static.request.send",
        "static.process.join",
    ]
    assert {403, 404, 429}.isdisjoint(_collector_settings(request.limits)["RETRY_HTTP_CODES"])


def test_static_cancellation_kills_a_stubborn_child_and_allows_a_new_execution() -> None:
    first_request = _request()
    second_request = _request(UUID("00000000-0000-4000-8000-000000004802"))
    context = _StaticProcessContext(stubborn=True)
    collector = StaticScrapyCollector(process_context=context, monotonic=lambda: 0.0)

    cancelled = collector.fetch(first_request, is_cancelled=lambda: True)
    retried = collector.fetch(second_request, is_cancelled=lambda: True)

    assert cancelled.failure_code is StaticFetchFailureCode.CANCELLED
    assert retried.failure_code is StaticFetchFailureCode.CANCELLED
    assert len(context.processes) == 2
    assert [connection.sent for connection in context.parent_inputs] == [
        [first_request],
        [second_request],
    ]
    assert context.events[:7] == [
        "static.process.start",
        "static.request.send",
        "static.process.terminate",
        "static.process.join",
        "static.process.kill",
        "static.process.join",
        "static.process.join",
    ]


def test_rendering_requires_an_explicit_approved_js_decision_before_launch() -> None:
    runtime = _runtime()
    resolver_calls: list[str] = []

    result = _rendered_collector(
        runtime,
        resolver_calls,
        approved_js_urls=frozenset(),
    ).fetch(_request())

    assert result.candidate is None
    assert result.failure_code is StaticFetchFailureCode.SOURCE_POLICY_BLOCKED
    assert runtime.launches == 0
    assert runtime.events == []
    assert resolver_calls == []


def test_approved_rendering_revalidates_redirect_dns_peer_and_every_subrequest() -> None:
    runtime = _runtime()
    resolver_calls: list[str] = []

    result = _rendered_collector(
        runtime,
        resolver_calls,
        approved_js_urls=frozenset({_SOURCE_URL}),
    ).fetch(_request())

    assert result.failure_code is None
    assert result.candidate is not None
    assert result.candidate.representation is Representation.HTML
    assert result.candidate.document.text == _RENDERED_HTML
    assert resolver_calls == [
        "synthetic-rendering.test",
        "synthetic-rendering.test",
        "assets.synthetic-rendering.test",
    ]
    assert runtime.routed_urls == [_SOURCE_URL, _REDIRECT_URL, _SUBREQUEST_URL]
    assert runtime.events[:8] == [
        "runtime.launch",
        "browser.new_context",
        "context.new_page",
        "page.route:**/*",
        f"page.goto:{_SOURCE_URL}",
        f"route.continue:{_SOURCE_URL}",
        f"route.continue:{_REDIRECT_URL}",
        f"route.continue:{_SUBREQUEST_URL}",
    ]


def test_rendering_aborts_an_unsafe_subrequest_before_content_is_read() -> None:
    unsafe_subrequest = _BrowserRequest(_SUBREQUEST_URL, "127.0.0.1", "script")
    runtime = _runtime(
        requests=[_BrowserRequest(_SOURCE_URL, _PUBLIC_IP, "document"), unsafe_subrequest]
    )
    resolver_calls: list[str] = []

    result = _rendered_collector(
        runtime,
        resolver_calls,
        approved_js_urls=frozenset({_SOURCE_URL}),
    ).fetch(_request())

    assert result.candidate is None
    assert result.failure_code is StaticFetchFailureCode.UNSAFE_DESTINATION
    assert runtime.routed_urls == [_SOURCE_URL, _SUBREQUEST_URL]
    assert f"route.abort:{_SUBREQUEST_URL}" in runtime.events
    assert "page.content" not in runtime.events
    assert resolver_calls == ["synthetic-rendering.test", "assets.synthetic-rendering.test"]


def test_rendering_aborts_a_redirect_when_its_peer_differs_from_its_dns_result() -> None:
    runtime = _runtime(
        requests=[
            _BrowserRequest(_SOURCE_URL, _PUBLIC_IP, "document"),
            _BrowserRequest(_REDIRECT_URL, "1.1.1.1", "document"),
        ]
    )
    resolver_calls: list[str] = []

    result = _rendered_collector(
        runtime,
        resolver_calls,
        approved_js_urls=frozenset({_SOURCE_URL}),
    ).fetch(_request())

    assert result.candidate is None
    assert result.failure_code is StaticFetchFailureCode.UNSAFE_DESTINATION
    assert runtime.routed_urls == [_SOURCE_URL, _REDIRECT_URL]
    assert f"route.abort:{_REDIRECT_URL}" in runtime.events
    assert "page.content" not in runtime.events
    assert resolver_calls == ["synthetic-rendering.test", "synthetic-rendering.test"]


def test_rendering_aborts_a_redirect_that_reresolves_to_a_private_address() -> None:
    private_redirect = "https://private.synthetic-rendering.test/rendered"
    runtime = _runtime(
        requests=[
            _BrowserRequest(_SOURCE_URL, _PUBLIC_IP, "document"),
            _BrowserRequest(private_redirect, _PUBLIC_IP, "document"),
        ]
    )
    resolver_calls: list[str] = []

    def resolver(hostname: str) -> Iterable[str]:
        resolver_calls.append(hostname)
        if hostname == "private.synthetic-rendering.test":
            return ("127.0.0.1",)
        return (_PUBLIC_IP,)

    result = _rendered_collector(
        runtime,
        resolver_calls,
        approved_js_urls=frozenset({_SOURCE_URL}),
        resolver=resolver,
    ).fetch(_request())

    assert result.candidate is None
    assert result.failure_code is StaticFetchFailureCode.UNSAFE_DESTINATION
    assert runtime.routed_urls == [_SOURCE_URL, private_redirect]
    assert f"route.abort:{private_redirect}" in runtime.events
    assert "page.content" not in runtime.events
    assert resolver_calls == ["synthetic-rendering.test", "private.synthetic-rendering.test"]


def test_rendering_uses_a_fresh_nonpersistent_context_with_workers_and_downloads_disabled() -> None:
    runtime = _runtime(requests=[_BrowserRequest(_SOURCE_URL, _PUBLIC_IP, "document")])
    resolver_calls: list[str] = []
    collector = _rendered_collector(
        runtime,
        resolver_calls,
        approved_js_urls=frozenset({_SOURCE_URL}),
    )

    first = collector.fetch(_request())
    second = collector.fetch(_request(UUID("00000000-0000-4000-8000-000000004803")))

    assert first.candidate is not None
    assert second.candidate is not None
    assert runtime.context_options == [
        {"service_workers": "block", "accept_downloads": False},
        {"service_workers": "block", "accept_downloads": False},
    ]
    assert runtime.storage_state_reads == 0
    assert runtime.cookie_reads == 0
    assert runtime.events.count("browser.new_context") == 2
    first_context = runtime.events.index("browser.new_context")
    second_context = runtime.events.index("browser.new_context", first_context + 1)
    _assert_in_order(
        runtime.events[:second_context],
        ["page.close", "context.close", "browser.close", "runtime.reap"],
    )


def test_rendered_429_is_one_browser_dispatch_without_a_candidate() -> None:
    runtime = _runtime(
        document="",
        requests=[_BrowserRequest(_SOURCE_URL, _PUBLIC_IP, "document")],
        response=_response(status=429, headers={"Retry-After": "120"}),
    )
    resolver_calls: list[str] = []

    result = _rendered_collector(
        runtime,
        resolver_calls,
        approved_js_urls=frozenset({_SOURCE_URL}),
    ).fetch(_request())

    assert result.candidate is None
    assert result.failure_code is StaticFetchFailureCode.RATE_LIMITED
    assert result.retry_after == "120"
    assert runtime.routed_urls == [_SOURCE_URL]
    assert runtime.events.count(f"page.goto:{_SOURCE_URL}") == 1
    assert runtime.events.count(f"route.continue:{_SOURCE_URL}") == 1


def test_rendering_cancellation_closes_resources_then_reaps_stubborn_child_before_reuse() -> None:
    cancelled = False

    def cancel_after_navigation() -> None:
        nonlocal cancelled
        cancelled = True

    runtime = _runtime(
        requests=[_BrowserRequest(_SOURCE_URL, _PUBLIC_IP, "document")],
        stubborn_child=True,
        on_goto=cancel_after_navigation,
    )
    resolver_calls: list[str] = []
    collector = _rendered_collector(
        runtime,
        resolver_calls,
        approved_js_urls=frozenset({_SOURCE_URL}),
    )

    stopped = collector.fetch(_request(), is_cancelled=lambda: cancelled)
    events_after_stop = list(runtime.events)
    runtime.on_goto = None
    resumed = collector.fetch(_request(UUID("00000000-0000-4000-8000-000000004804")))

    assert stopped.failure_code is StaticFetchFailureCode.CANCELLED
    assert runtime.routed_urls[:1] == [_SOURCE_URL]
    _assert_in_order(
        events_after_stop,
        [
            "page.close",
            "context.close",
            "browser.close",
            "runtime.reap",
            "child.terminate",
            "child.join",
            "child.kill",
            "child.join",
        ],
    )
    assert resumed.candidate is not None
    assert runtime.routed_urls == [_SOURCE_URL, _SOURCE_URL]


def test_real_browser_rendering_remains_explicitly_gated() -> None:
    pytest.importorskip("playwright.async_api", reason=_REAL_BROWSER_GATE_REASON)
    pytest.skip(_REAL_BROWSER_GATE_REASON)
