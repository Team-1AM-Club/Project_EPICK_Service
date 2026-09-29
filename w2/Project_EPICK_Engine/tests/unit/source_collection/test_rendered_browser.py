from __future__ import annotations

import os

import pytest
from tests.integration.source_collection.test_rendering_safety import _request

from epick_engine.source_collection.collector import RenderedCollector
from epick_engine.source_collection.rendered_browser import (
    BrowserFetchResponse,
    PlaywrightBrowserRuntime,
)


@pytest.mark.local_browser
def test_real_offline_browser_executes_js_only_from_reviewed_fetches() -> None:
    pytest.importorskip("playwright.sync_api")
    if (
        not os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        or os.environ.get("EPICK_LOCAL_BROWSER_APPROVED") != "1"
    ):
        pytest.skip("isolated Playwright browser path is required")

    class ReviewedFetcher:
        calls: list[str] = []

        def fetch(
            self, url: str, *, timeout_seconds: float, max_bytes: int
        ) -> BrowserFetchResponse:
            del timeout_seconds, max_bytes
            self.calls.append(url)
            if url != _request().source_url:
                raise AssertionError("unexpected browser request")
            body = (
                b"<html><body><main id='root'></main><script>"
                b"document.querySelector('#root').textContent='Rendered by Chromium';"
                b"</script></body></html>"
            )
            return BrowserFetchResponse(
                url=url,
                status=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=body,
                peer_address="8.8.8.8",
            )

    request = _request()
    fetcher = ReviewedFetcher()
    runtime = PlaywrightBrowserRuntime(limits=request.limits, fetcher=fetcher)
    result = RenderedCollector(
        browser_runtime=runtime,
        resolver=lambda _hostname: ("8.8.8.8",),
        approved_js_urls=frozenset({request.source_url}),
    ).fetch(request)

    assert result.candidate is not None
    assert "Rendered by Chromium" in result.candidate.document.text
    assert fetcher.calls == [request.source_url]
