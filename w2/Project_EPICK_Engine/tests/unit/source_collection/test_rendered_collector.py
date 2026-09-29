from __future__ import annotations

from tests.integration.source_collection.test_rendering_safety import (
    _PUBLIC_IP,
    _BrowserRequest,
    _rendered_collector,
    _request,
    _runtime,
)

from epick_engine.source_collection.collector import StaticFetchFailureCode


def test_rendered_collector_requires_explicit_strategy() -> None:
    request = _request()
    denied_runtime = _runtime()
    denied = _rendered_collector(denied_runtime, [], approved_js_urls=frozenset()).fetch(request)
    assert denied.failure_code is StaticFetchFailureCode.SOURCE_POLICY_BLOCKED
    assert denied_runtime.launches == 0

    allowed = _rendered_collector(
        _runtime(), [], approved_js_urls=frozenset({request.source_url})
    ).fetch(request)
    assert allowed.failure_code is None
    assert allowed.candidate is not None


def test_rendered_collector_timeout_is_bounded_failure() -> None:
    request = _request()

    def timeout() -> None:
        raise TimeoutError("synthetic browser deadline")

    result = _rendered_collector(
        _runtime(
            requests=[_BrowserRequest(request.source_url, _PUBLIC_IP, "document")],
            on_goto=timeout,
        ),
        [],
        approved_js_urls=frozenset({request.source_url}),
    ).fetch(request)

    assert result.failure_code is StaticFetchFailureCode.FETCH_TIMEOUT
    assert result.candidate is None


def test_rendered_collector_enforces_response_size_limit() -> None:
    request = _request()
    result = _rendered_collector(
        _runtime(
            document="x" * (request.limits.max_decompressed_bytes + 1),
            requests=[_BrowserRequest(request.source_url, _PUBLIC_IP, "document")],
        ),
        [],
        approved_js_urls=frozenset({request.source_url}),
    ).fetch(request)

    assert result.failure_code is StaticFetchFailureCode.RESPONSE_TOO_LARGE
    assert result.candidate is None
