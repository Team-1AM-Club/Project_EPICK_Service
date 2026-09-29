from __future__ import annotations

import warnings
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from scrapy.core.downloader.contextfactory import BrowserLikeContextFactory
from scrapy.exceptions import ScrapyDeprecationWarning

import epick_engine.source_collection.collector as collector_module
from epick_engine.source_collection.collector import (
    StaticDestinationValidationMiddleware,
    StaticFetchFailureCode,
    StaticFetchRequest,
    StaticFetchResult,
    StaticLimitHandoffMiddleware,
    StaticResponseCandidate,
    StaticResponseSnapshot,
    StaticScrapyCollector,
    _collector_settings,
    _evaluate_static_response,
    _exception_failure_code,
    _prepare_initial_target,
    _StaticRedirect,
)
from epick_engine.source_collection.policy import (
    AccessClass,
    ExecutionLimits,
    ExecutionPolicyUnconfigured,
    OfficialStatus,
    Permission,
    PolicyBlocked,
    PolicySnapshot,
    Representation,
    UnsafeDestination,
    UntrustedDocument,
    ValidatedTarget,
    validate_url,
)

_COMMAND_ID = UUID("00000000-0000-4000-8000-000000000701")
_PUBLIC_IP = "8.8.8.8"
_URL = "https://official.test/careers"


def _resolver(*addresses: str):
    def resolve(_hostname: str) -> Iterable[str]:
        return addresses

    return resolve


def _policy(**overrides: object) -> PolicySnapshot:
    values: dict[str, object] = {
        "official_status": OfficialStatus.VERIFIED,
        "access_class": AccessClass.PUBLIC,
        "collection_permission": Permission.ALLOWED,
        "excerpt_storage_permission": Permission.ALLOWED,
        "body_storage_permission": Permission.DENIED,
        "redistribution_permission": Permission.DENIED,
        "revision": 3,
    }
    values.update(overrides)
    return PolicySnapshot(**values)


def _limits(**overrides: int | float) -> ExecutionLimits:
    values: dict[str, int | float] = {
        "site_concurrency": 2,
        "global_concurrency": 8,
        "source_ttl_seconds": 3600,
        "max_response_bytes": 100,
        "max_decompressed_bytes": 200,
        "connect_timeout_seconds": 3.0,
        "read_timeout_seconds": 9.0,
        "max_redirects": 2,
        "general_retry_limit": 1,
        "retention_days": 30,
    }
    values.update(overrides)
    return ExecutionLimits(**values)


def _request(**overrides: object) -> StaticFetchRequest:
    values: dict[str, object] = {
        "command_id": _COMMAND_ID,
        "source_url": _URL,
        "policy": _policy(),
        "robots_permission": Permission.ALLOWED,
        "limits": _limits(),
    }
    values.update(overrides)
    return StaticFetchRequest(**values)


def _target(url: str = _URL):
    return validate_url(url, _resolver(_PUBLIC_IP))


def _snapshot(
    *,
    url: str = _URL,
    status_code: int = 200,
    content_type: str = "text/html; charset=utf-8",
    body: bytes = b"<html><body>Official job posting</body></html>",
    peer_address: str | None = _PUBLIC_IP,
    raw_size: int | None = None,
    headers: dict[str, str] | None = None,
) -> StaticResponseSnapshot:
    response_headers = {"Content-Type": content_type}
    response_headers.update(headers or {})
    return StaticResponseSnapshot(
        url=url,
        status_code=status_code,
        headers=response_headers,
        body=body,
        peer_address=peer_address,
        raw_size=len(body) if raw_size is None else raw_size,
    )


@pytest.mark.parametrize(
    ("fetch_request", "expected_exception"),
    [
        (
            _request(policy=_policy(collection_permission=Permission.DENIED)),
            PolicyBlocked,
        ),
        (_request(robots_permission=Permission.UNKNOWN), PolicyBlocked),
    ],
)
def test_policy_and_robots_fail_before_dns(
    fetch_request: StaticFetchRequest,
    expected_exception: type[Exception],
) -> None:
    resolver_calls: list[str] = []

    def resolver(hostname: str) -> Iterable[str]:
        resolver_calls.append(hostname)
        return (_PUBLIC_IP,)

    with pytest.raises(expected_exception):
        _prepare_initial_target(fetch_request, resolver)

    assert resolver_calls == []


def test_initial_target_is_validated_after_policy_authorization() -> None:
    target = _prepare_initial_target(_request(), _resolver(_PUBLIC_IP))

    assert target.url == _URL
    assert target.resolved_addresses == frozenset({_PUBLIC_IP})


def test_request_revalidates_shared_execution_limits() -> None:
    invalid = _limits()
    object.__setattr__(invalid, "connect_timeout_seconds", float("inf"))

    with pytest.raises(ExecutionPolicyUnconfigured):
        _request(limits=invalid)


@pytest.mark.parametrize(
    "limits",
    [
        _limits(
            connect_timeout_seconds=1e308,
            read_timeout_seconds=1e308,
            general_retry_limit=1,
            max_redirects=1,
        ),
        _limits(general_retry_limit=10**400),
    ],
)
def test_request_rejects_non_finite_total_deadline(limits: ExecutionLimits) -> None:
    with pytest.raises(ExecutionPolicyUnconfigured):
        _request(limits=limits)


def test_scrapy_settings_are_fail_closed_and_exclude_429() -> None:
    settings = _collector_settings(_limits())

    assert settings["REDIRECT_ENABLED"] is False
    assert settings["METAREFRESH_ENABLED"] is False
    assert settings["HTTPERROR_ALLOW_ALL"] is True
    assert settings["ROBOTSTXT_OBEY"] is False
    assert settings["LOG_ENABLED"] is False
    assert settings["HTTPPROXY_ENABLED"] is False
    assert settings["RETRY_TIMES"] == 1
    assert settings["REMOTE_CONTROL_ENABLED"] is False
    assert 429 not in settings["RETRY_HTTP_CODES"]
    assert settings["DOWNLOAD_MAXSIZE"] == 100
    assert settings["DOWNLOAD_TIMEOUT"] == 3.0
    assert (
        settings["DOWNLOADER_MIDDLEWARES"][
            "epick_engine.source_collection.collector.StaticDestinationValidationMiddleware"
        ]
        == 650
    )
    assert (
        settings["DOWNLOADER_MIDDLEWARES"][
            "epick_engine.source_collection.collector.StaticLimitHandoffMiddleware"
        ]
        == 591
    )


def test_scrapy_settings_use_certificate_verifying_tls_context() -> None:
    settings = _collector_settings(_limits())

    assert settings["DOWNLOADER_CLIENTCONTEXTFACTORY"] == (
        "scrapy.core.downloader.contextfactory.BrowserLikeContextFactory"
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ScrapyDeprecationWarning)
        context_factory = BrowserLikeContextFactory()
    tls_options = context_factory.creatorForNetloc(b"official.test", 443)
    certificate_options = tls_options._createConnection.__self__

    assert certificate_options.verify is True


def test_limit_handoff_uses_independent_raw_and_decompressed_caps() -> None:
    middleware = StaticLimitHandoffMiddleware()
    request = SimpleNamespace(meta={"static_raw_maxsize": 100, "static_decompressed_maxsize": 200})
    response = SimpleNamespace(body=b"compressed")

    middleware.process_request(request, object())
    assert request.meta["download_maxsize"] == 100

    returned = middleware.process_response(request, response, object())

    assert returned is response
    assert request.meta["static_raw_size"] == len(response.body)
    assert request.meta["download_maxsize"] == 200


def test_destination_middleware_revalidates_each_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def resolver(hostname: str) -> tuple[str, ...]:
        calls.append(hostname)
        return (_PUBLIC_IP,)

    monkeypatch.setattr(collector_module, "_system_resolver", resolver)
    request = SimpleNamespace(url=_URL, meta={})
    middleware = StaticDestinationValidationMiddleware()

    middleware.process_request(request, object())
    middleware.process_request(request, object())

    assert calls == ["official.test", "official.test"]
    assert request.meta["static_target"].resolved_addresses == frozenset({_PUBLIC_IP})


def test_destination_middleware_checks_peer_before_retry_middleware() -> None:
    request = SimpleNamespace(meta={"static_target": _target()})
    response = SimpleNamespace(ip_address="1.1.1.1", status=500)

    with pytest.raises(UnsafeDestination):
        StaticDestinationValidationMiddleware().process_response(
            request,
            response,
            object(),
        )


@pytest.mark.parametrize(
    ("content_type", "body", "representation"),
    [
        ("text/html", b"<main>Official job posting</main>", Representation.HTML),
        ("application/json", b'{"title":"Engineer"}', Representation.JSON),
    ],
)
def test_supported_response_becomes_only_an_untrusted_candidate(
    content_type: str,
    body: bytes,
    representation: Representation,
) -> None:
    decision = _evaluate_static_response(
        _request(),
        _snapshot(content_type=content_type, body=body),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is None
    assert decision.candidate is not None
    assert decision.candidate.representation is representation
    assert isinstance(decision.candidate.document, UntrustedDocument)
    assert decision.candidate.document.text


@pytest.mark.parametrize("charset", ["unicode_escape", "raw_unicode_escape"])
def test_transform_charset_falls_back_to_utf8_without_surrogates(charset: str) -> None:
    body = rb"<main>\ud800 Official job posting</main>"

    decision = _evaluate_static_response(
        _request(),
        _snapshot(content_type=f"text/html; charset={charset}", body=body),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is None
    assert decision.candidate is not None
    assert decision.candidate.document.text.encode() == body


def test_invalid_unicode_scalar_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_scalar(_body: bytes, _content_type: str) -> str:
        raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")

    monkeypatch.setattr(collector_module, "_decode_body", invalid_scalar)

    decision = _evaluate_static_response(
        _request(),
        _snapshot(),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.EXTRACTION_FAILED
    assert decision.candidate is None


@pytest.mark.parametrize("peer_address", [None, "127.0.0.1", "1.1.1.1"])
def test_missing_private_or_mismatched_peer_fails_closed(
    peer_address: str | None,
) -> None:
    decision = _evaluate_static_response(
        _request(),
        _snapshot(peer_address=peer_address),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.UNSAFE_DESTINATION


def test_allowed_redirect_is_revalidated_and_increments_hop() -> None:
    decision = _evaluate_static_response(
        _request(
            redirect_robots_permissions=(("https://redirected.test/job", Permission.ALLOWED),)
        ),
        _snapshot(
            status_code=302,
            headers={"Location": "https://redirected.test/job"},
        ),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, _StaticRedirect)
    assert decision.target.url == "https://redirected.test/job"
    assert decision.redirect_hops == 1


@pytest.mark.parametrize(
    ("location", "hops"),
    [
        ("http://redirected.test/job", 0),
        ("https://127.0.0.1/job", 0),
        ("https://redirected.test/job", 2),
    ],
)
def test_unsafe_or_exhausted_redirect_fails_closed(
    location: str,
    hops: int,
) -> None:
    decision = _evaluate_static_response(
        _request(redirect_robots_permissions=((location, Permission.ALLOWED),)),
        _snapshot(status_code=302, headers={"Location": location}),
        _target(),
        hops,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.UNSAFE_DESTINATION


def test_redirect_without_path_specific_robots_permission_is_blocked() -> None:
    decision = _evaluate_static_response(
        _request(),
        _snapshot(
            status_code=302,
            headers={"Location": "https://redirected.test/job"},
        ),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.SOURCE_POLICY_BLOCKED


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, StaticFetchFailureCode.ACCESS_DENIED),
        (403, StaticFetchFailureCode.ACCESS_DENIED),
        (404, StaticFetchFailureCode.NOT_FOUND),
        (500, StaticFetchFailureCode.FETCH_FAILED),
    ],
)
def test_http_failures_never_become_candidates(
    status: int,
    expected: StaticFetchFailureCode,
) -> None:
    decision = _evaluate_static_response(
        _request(),
        _snapshot(status_code=status),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is expected


def test_429_is_rate_limited_without_a_candidate() -> None:
    decision = _evaluate_static_response(
        _request(),
        _snapshot(status_code=429, headers={"Retry-After": "120"}),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.RATE_LIMITED
    assert decision.retry_after == "120"


@pytest.mark.parametrize(
    ("url", "content_type"),
    [
        ("https://official.test/report.pdf", "text/html"),
        (_URL, "application/pdf"),
    ],
)
def test_pdf_is_never_a_candidate(url: str, content_type: str) -> None:
    decision = _evaluate_static_response(
        _request(),
        _snapshot(url=url, content_type=content_type),
        _target(url),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.UNSUPPORTED_FORMAT


@pytest.mark.parametrize(
    "body",
    [
        b"%PDF-1.7 synthetic",
        b"\xef\xbb\xbf%PDF-1.7 synthetic",
    ],
)
def test_pdf_magic_is_rejected_even_when_mislabeled_as_html(body: bytes) -> None:
    decision = _evaluate_static_response(
        _request(),
        _snapshot(content_type="text/html", body=body),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.UNSUPPORTED_FORMAT


def test_synthetic_static_posting_is_only_an_untrusted_candidate() -> None:
    fixture = (
        Path(__file__).parents[2] / "fixtures" / "synthetic_sources" / "static_posting.html"
    ).read_bytes()
    decision = _evaluate_static_response(
        _request(
            limits=_limits(
                max_response_bytes=len(fixture),
                max_decompressed_bytes=len(fixture),
            )
        ),
        _snapshot(body=fixture),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is None
    assert decision.candidate is not None
    assert isinstance(decision.candidate.document, UntrustedDocument)


@pytest.mark.parametrize(
    "snapshot",
    [
        _snapshot(raw_size=101),
        _snapshot(body=b"x" * 201, raw_size=100),
    ],
)
def test_raw_and_decompressed_size_caps_are_independent(
    snapshot: StaticResponseSnapshot,
) -> None:
    decision = _evaluate_static_response(
        _request(),
        snapshot,
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.RESPONSE_TOO_LARGE


@pytest.mark.parametrize(
    "body",
    [
        b"<html><script>render()</script></html>",
        b'<form action="/login"><input type="password"></form>',
        b'<form action="/sign-in"><input name="email"></form>',
        b"<html><title>Error</title><body>Try later</body></html>",
        b"<html><h1>Error: service unavailable</h1></html>",
        b"<html><body>Access denied</body></html>",
    ],
)
def test_login_error_and_empty_shell_are_not_candidates(body: bytes) -> None:
    decision = _evaluate_static_response(
        _request(),
        _snapshot(body=body),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.EXTRACTION_FAILED


def test_empty_json_is_not_a_candidate() -> None:
    decision = _evaluate_static_response(
        _request(),
        _snapshot(content_type="application/json", body=b"  "),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    assert decision.failure_code is StaticFetchFailureCode.EXTRACTION_FAILED


def test_compression_ignore_request_maps_to_response_too_large() -> None:
    class IgnoreRequest(Exception):
        pass

    assert _exception_failure_code(IgnoreRequest()) is StaticFetchFailureCode.RESPONSE_TOO_LARGE


def test_result_requires_exactly_one_candidate_or_failure() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        StaticFetchResult(_COMMAND_ID, None, None)

    candidate = StaticResponseCandidate(
        final_target=_target(),
        representation=Representation.HTML,
        document=UntrustedDocument("public"),
        http_status=200,
        raw_size=6,
        decompressed_size=6,
    )
    with pytest.raises(ValueError, match="exactly one"):
        StaticFetchResult(
            _COMMAND_ID,
            candidate,
            StaticFetchFailureCode.FETCH_FAILED,
        )


class _FakeConnection:
    def __init__(self, values: list[object] | None = None) -> None:
        self.values = list(values or ())
        self.sent: list[object] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def poll(self, _timeout: float = 0.0) -> bool:
        return bool(self.values)

    def recv(self) -> object:
        return self.values.pop(0)

    def send(self, value: object) -> None:
        self.sent.append(value)


class _FakeProcess:
    def __init__(self, *, exits_on_start: bool = False) -> None:
        self.exits_on_start = exits_on_start
        self.started = False
        self.alive = False
        self.terminate_count = 0
        self.kill_count = 0
        self.join_count = 0

    def start(self) -> None:
        self.started = True
        self.alive = not self.exits_on_start

    def is_alive(self) -> bool:
        return self.alive

    def join(self, _timeout: float | None = None) -> None:
        self.join_count += 1
        if self.terminate_count or self.kill_count:
            self.alive = False
        elif self.started:
            self.alive = False

    def terminate(self) -> None:
        self.terminate_count += 1
        self.alive = False

    def kill(self) -> None:
        self.kill_count += 1
        self.alive = False


class _FakeContext:
    def __init__(
        self,
        result_values: list[object] | None = None,
        *,
        exits_on_start: bool = False,
    ) -> None:
        self.child_input = _FakeConnection()
        self.parent_input = _FakeConnection()
        self.parent_output = _FakeConnection(result_values)
        self.child_output = _FakeConnection()
        self.process = _FakeProcess(exits_on_start=exits_on_start)
        self.process_target: object | None = None
        self.process_args: tuple[object, ...] = ()
        self.pipe_count = 0

    def Pipe(self, duplex: bool = True) -> tuple[_FakeConnection, _FakeConnection]:
        assert duplex is False
        self.pipe_count += 1
        if self.pipe_count == 1:
            return self.child_input, self.parent_input
        return self.parent_output, self.child_output

    def Process(
        self,
        *,
        target: object,
        args: tuple[object, ...],
        daemon: bool,
    ) -> _FakeProcess:
        assert daemon is False
        self.process_target = target
        self.process_args = args
        return self.process


def test_child_disables_logging_before_receiving_fetch_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled_levels: list[int] = []
    input_connection = _FakeConnection([object()])
    output_connection = _FakeConnection()
    monkeypatch.setattr(collector_module.logging, "disable", disabled_levels.append)

    collector_module._run_static_child(input_connection, output_connection)

    assert disabled_levels == [collector_module.logging.CRITICAL]
    assert input_connection.closed is True
    assert output_connection.closed is True


def test_parent_sends_sensitive_request_only_after_child_start() -> None:
    request = _request(source_url="https://secret-url.test/path")
    expected = StaticFetchResult(
        _COMMAND_ID,
        None,
        StaticFetchFailureCode.FETCH_FAILED,
    )
    context = _FakeContext([expected])
    collector = StaticScrapyCollector(process_context=context)

    result = collector.fetch(request)

    assert result is expected
    assert context.process.started is True
    assert context.parent_input.sent == [request]
    assert context.process_args == (context.child_input, context.child_output)
    assert "secret-url" not in repr(context.process_args)
    assert context.process.join_count == 1


def test_cancellation_terminates_and_joins_child() -> None:
    context = _FakeContext()
    collector = StaticScrapyCollector(process_context=context)

    result = collector.fetch(_request(), is_cancelled=lambda: True)

    assert result.failure_code is StaticFetchFailureCode.CANCELLED
    assert context.process.terminate_count == 1
    assert context.process.join_count >= 1
    assert context.process.is_alive() is False


def test_deadline_terminates_and_joins_child() -> None:
    times = iter([0.0, 1_000.0])
    context = _FakeContext()
    collector = StaticScrapyCollector(
        process_context=context,
        monotonic=lambda: next(times),
    )

    result = collector.fetch(_request())

    assert result.failure_code is StaticFetchFailureCode.FETCH_TIMEOUT
    assert context.process.terminate_count == 1
    assert context.process.is_alive() is False


@pytest.mark.parametrize(
    "frame",
    [
        object(),
        StaticFetchResult(
            UUID("00000000-0000-4000-8000-000000000799"),
            None,
            StaticFetchFailureCode.FETCH_FAILED,
        ),
    ],
)
def test_malformed_or_mismatched_child_frame_fails_closed(frame: object) -> None:
    context = _FakeContext([frame], exits_on_start=True)
    collector = StaticScrapyCollector(process_context=context)

    result = collector.fetch(_request())

    assert result.failure_code is StaticFetchFailureCode.FETCH_FAILED
    assert context.process.join_count == 1


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("raw_size", 101, StaticFetchFailureCode.RESPONSE_TOO_LARGE),
        ("decompressed_size", 201, StaticFetchFailureCode.RESPONSE_TOO_LARGE),
        ("raw_size", -1, StaticFetchFailureCode.FETCH_FAILED),
    ],
)
def test_parent_revalidates_bounded_candidate_frame(
    field: str,
    value: int,
    expected: StaticFetchFailureCode,
) -> None:
    candidate = StaticResponseCandidate(
        final_target=_target(),
        representation=Representation.HTML,
        document=UntrustedDocument("public"),
        http_status=200,
        raw_size=6,
        decompressed_size=6,
    )
    object.__setattr__(candidate, field, value)
    frame = StaticFetchResult(_COMMAND_ID, candidate, None)
    context = _FakeContext([frame], exits_on_start=True)
    collector = StaticScrapyCollector(process_context=context)

    result = collector.fetch(_request())

    assert result.failure_code is expected
    assert context.process.join_count == 1


def test_parent_revalidates_actual_candidate_document_size() -> None:
    candidate = StaticResponseCandidate(
        final_target=_target(),
        representation=Representation.HTML,
        document=UntrustedDocument("x" * 201),
        http_status=200,
        raw_size=6,
        decompressed_size=6,
    )
    frame = StaticFetchResult(_COMMAND_ID, candidate, None)
    context = _FakeContext([frame], exits_on_start=True)
    collector = StaticScrapyCollector(process_context=context)

    result = collector.fetch(_request())

    assert result.failure_code is StaticFetchFailureCode.RESPONSE_TOO_LARGE
    assert context.process.join_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document", object()),
        ("final_target", object()),
        ("representation", "HTML"),
        ("http_status", 500),
    ],
)
def test_parent_rejects_malformed_nested_candidate(
    field: str,
    value: object,
) -> None:
    candidate = StaticResponseCandidate(
        final_target=_target(),
        representation=Representation.HTML,
        document=UntrustedDocument("public"),
        http_status=200,
        raw_size=6,
        decompressed_size=6,
    )
    object.__setattr__(candidate, field, value)
    frame = StaticFetchResult(_COMMAND_ID, candidate, None)
    context = _FakeContext([frame], exits_on_start=True)

    result = StaticScrapyCollector(process_context=context).fetch(_request())

    assert result.failure_code is StaticFetchFailureCode.FETCH_FAILED
    assert context.process.join_count == 1


@pytest.mark.parametrize(
    "target",
    [
        ValidatedTarget(
            "http://official.test/careers",
            "official.test",
            80,
            frozenset({_PUBLIC_IP}),
        ),
        ValidatedTarget(
            "https://127.0.0.1/careers",
            "127.0.0.1",
            443,
            frozenset({"127.0.0.1"}),
        ),
        ValidatedTarget(
            _URL,
            "different.test",
            443,
            frozenset({_PUBLIC_IP}),
        ),
    ],
)
def test_parent_rejects_semantically_invalid_target(target: ValidatedTarget) -> None:
    candidate = StaticResponseCandidate(
        final_target=target,
        representation=Representation.HTML,
        document=UntrustedDocument("public"),
        http_status=200,
        raw_size=6,
        decompressed_size=6,
    )
    frame = StaticFetchResult(_COMMAND_ID, candidate, None)
    context = _FakeContext([frame], exits_on_start=True)

    result = StaticScrapyCollector(process_context=context).fetch(_request())

    assert result.failure_code is StaticFetchFailureCode.FETCH_FAILED
    assert context.process.join_count == 1


@pytest.mark.parametrize(
    ("representation", "text"),
    [
        (Representation.HTML, ""),
        (Representation.HTML, '<form action="/login"><input name="email"></form>'),
        (Representation.HTML, "%PDF-1.7"),
        (Representation.JSON, "   "),
    ],
)
def test_parent_rejects_semantically_invalid_document(
    representation: Representation,
    text: str,
) -> None:
    candidate = StaticResponseCandidate(
        final_target=_target(),
        representation=representation,
        document=UntrustedDocument(text),
        http_status=200,
        raw_size=len(text.encode("utf-8")),
        decompressed_size=len(text.encode("utf-8")),
    )
    frame = StaticFetchResult(_COMMAND_ID, candidate, None)
    context = _FakeContext([frame], exits_on_start=True)

    result = StaticScrapyCollector(process_context=context).fetch(_request())

    assert result.failure_code is StaticFetchFailureCode.FETCH_FAILED
    assert context.process.join_count == 1


def test_parent_rejects_malformed_document_text() -> None:
    document = UntrustedDocument("public")
    object.__setattr__(document, "text", None)
    candidate = StaticResponseCandidate(
        final_target=_target(),
        representation=Representation.HTML,
        document=document,
        http_status=200,
        raw_size=6,
        decompressed_size=6,
    )
    frame = StaticFetchResult(_COMMAND_ID, candidate, None)
    context = _FakeContext([frame], exits_on_start=True)

    result = StaticScrapyCollector(process_context=context).fetch(_request())

    assert result.failure_code is StaticFetchFailureCode.FETCH_FAILED
    assert context.process.join_count == 1


def test_parent_rejects_malformed_result_invariant() -> None:
    candidate = StaticResponseCandidate(
        final_target=_target(),
        representation=Representation.HTML,
        document=UntrustedDocument("public"),
        http_status=200,
        raw_size=6,
        decompressed_size=6,
    )
    frame = StaticFetchResult(_COMMAND_ID, candidate, None)
    object.__setattr__(frame, "failure_code", StaticFetchFailureCode.ACCESS_DENIED)
    context = _FakeContext([frame], exits_on_start=True)

    result = StaticScrapyCollector(process_context=context).fetch(_request())

    assert result.failure_code is StaticFetchFailureCode.FETCH_FAILED
    assert context.process.join_count == 1


def test_close_is_idempotent_and_prevents_new_children() -> None:
    context = _FakeContext()
    collector = StaticScrapyCollector(process_context=context)

    collector.close()
    collector.close()

    with pytest.raises(RuntimeError, match="closed"):
        collector.fetch(_request())
    assert context.process.started is False


def test_response_body_and_token_are_never_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sentinel = "PRIVATE-BODY-TOKEN-7B21"
    decision = _evaluate_static_response(
        _request(),
        _snapshot(body=f"<main>{sentinel}</main>".encode()),
        _target(),
        0,
        _resolver(_PUBLIC_IP),
    )

    captured = capsys.readouterr()
    assert isinstance(decision, StaticFetchResult)
    assert captured.out == ""
    assert captured.err == ""
    assert sentinel not in captured.out + captured.err
