"""Offline Chromium adapter with a separately validated, IP-pinned HTTPS fetch path.

Chromium is always offline: intercepted requests are fulfilled from this module's
bounded HTTPS fetcher, never continued to the browser network stack. This also
blocks browser WebSockets and other non-intercepted egress.
"""

from __future__ import annotations

import http.client
import logging
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from epick_engine.source_collection.collector import (
    StaticFetchFailureCode,
    _system_resolver,
)
from epick_engine.source_collection.policy import (
    ExecutionLimits,
    UnsafeDestination,
    validate_connection_destination,
    validate_url,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BrowserFetchResponse:
    url: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    peer_address: str


class BrowserFetchError(RuntimeError):
    def __init__(self, code: StaticFetchFailureCode) -> None:
        super().__init__(code.value)
        self.code = code


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self, host: str, pinned_address: str, port: int, *, timeout: float
    ) -> None:
        self._ssl_context = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=self._ssl_context)
        self._pinned_address = pinned_address

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_address, self.port), self.timeout)
        try:
            self.sock = self._ssl_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class SafeHTTPSFetcher:
    """Make one GET with DNS validation, TLS hostname check and byte limits."""

    def fetch(
        self, url: str, *, timeout_seconds: float, max_bytes: int
    ) -> BrowserFetchResponse:
        try:
            target = validate_url(url, _system_resolver)
            address = sorted(target.resolved_addresses)[0]
            parsed = urlsplit(target.url)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            connection = _PinnedHTTPSConnection(
                target.hostname, address, target.port, timeout=timeout_seconds
            )
            try:
                connection.connect()
                if connection.sock is None:
                    raise UnsafeDestination("connected peer is unavailable")
                peer = connection.sock.getpeername()[0]
                validate_connection_destination(target, peer)
                connection.request(
                    "GET",
                    path,
                    headers={"Accept-Encoding": "identity", "Cookie": ""},
                )
                response = connection.getresponse()
                content_length = response.getheader("Content-Length")
                if content_length is not None and int(content_length) > max_bytes:
                    raise BrowserFetchError(StaticFetchFailureCode.RESPONSE_TOO_LARGE)
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise BrowserFetchError(StaticFetchFailureCode.UNSUPPORTED_FORMAT)
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise BrowserFetchError(StaticFetchFailureCode.RESPONSE_TOO_LARGE)
                allowed_headers = {
                    "content-type",
                    "location",
                    "retry-after",
                    "access-control-allow-origin",
                    "access-control-allow-methods",
                    "access-control-allow-headers",
                }
                headers = {
                    name: value
                    for name, value in response.getheaders()
                    if name.lower() in allowed_headers
                }
                return BrowserFetchResponse(
                    url=target.url,
                    status=response.status,
                    headers=headers,
                    body=body,
                    peer_address=peer,
                )
            finally:
                connection.close()
        except BrowserFetchError:
            raise
        except UnsafeDestination:
            raise BrowserFetchError(StaticFetchFailureCode.UNSAFE_DESTINATION) from None
        except TimeoutError:
            raise BrowserFetchError(StaticFetchFailureCode.FETCH_TIMEOUT) from None
        except (OSError, ValueError, ssl.SSLError, http.client.HTTPException):
            raise BrowserFetchError(StaticFetchFailureCode.FETCH_FAILED) from None


class _ReviewedRequest:
    def __init__(self, url: str, peer_address: str | None, failure_code: object) -> None:
        self.url = url
        self.peer_address = peer_address
        self.failure_code = failure_code


class _ReviewedRoute:
    def __init__(
        self,
        route: Any,
        *,
        fetcher: Any,
        limits: ExecutionLimits,
        responses: dict[str, BrowserFetchResponse],
    ) -> None:
        self._route = route
        self._response: BrowserFetchResponse | None = None
        self.request = _ReviewedRequest(route.request.url, None, None)
        if route.request.method != "GET":
            self.request.failure_code = StaticFetchFailureCode.UNSAFE_DESTINATION
            return
        try:
            self._response = fetcher.fetch(
                route.request.url,
                timeout_seconds=min(
                    limits.connect_timeout_seconds, limits.read_timeout_seconds
                ),
                max_bytes=limits.max_response_bytes,
            )
            self.request.peer_address = self._response.peer_address
            responses[route.request.url] = self._response
        except BrowserFetchError as exc:
            _LOGGER.debug(
                "reviewed browser fetch rejected scheme=%s host=%s code=%s",
                urlsplit(route.request.url).scheme,
                urlsplit(route.request.url).hostname,
                exc.code.value,
            )
            self.request.failure_code = exc.code

    def continue_(self) -> None:
        if self._response is None:
            raise RuntimeError("reviewed response is unavailable")
        self._route.fulfill(
            status=self._response.status,
            headers=dict(self._response.headers),
            body=self._response.body,
        )

    def abort(self) -> None:
        self._route.abort()


class _ReviewedPage:
    def __init__(self, page: Any, *, fetcher: Any, limits: ExecutionLimits) -> None:
        self._page = page
        self._fetcher = fetcher
        self._limits = limits
        self._responses: dict[str, BrowserFetchResponse] = {}

    def route(self, pattern: str, handler: Any) -> None:
        self._page.route(
            pattern,
            lambda route: handler(
                _ReviewedRoute(
                    route,
                    fetcher=self._fetcher,
                    limits=self._limits,
                    responses=self._responses,
                )
            ),
        )

    def goto(self, url: str) -> BrowserFetchResponse:
        timeout_ms = int(
            min(self._limits.connect_timeout_seconds + self._limits.read_timeout_seconds, 30)
            * 1000
        )
        response = self._page.goto(url, wait_until="load", timeout=timeout_ms)
        if response is None:
            raise RuntimeError("rendered navigation returned no response")
        self._page.wait_for_timeout(100)
        fetched = self._responses.get(response.url)
        if fetched is None:
            raise RuntimeError("rendered response was not reviewed")
        return fetched

    def content(self) -> str:
        return cast(str, self._page.content())

    def close(self) -> None:
        self._page.close()


class _ReviewedContext:
    def __init__(self, context: Any, *, fetcher: Any, limits: ExecutionLimits) -> None:
        self._context = context
        self._fetcher = fetcher
        self._limits = limits

    def new_page(self) -> _ReviewedPage:
        return _ReviewedPage(
            self._context.new_page(), fetcher=self._fetcher, limits=self._limits
        )

    def close(self) -> None:
        self._context.close()


class _ReviewedBrowser:
    def __init__(self, browser: Any, playwright: Any, *, fetcher: Any, limits: ExecutionLimits):
        self._browser = browser
        self._playwright = playwright
        self._fetcher = fetcher
        self._limits = limits

    def new_context(self, **options: object) -> _ReviewedContext:
        context = self._browser.new_context(offline=True, **options)
        return _ReviewedContext(context, fetcher=self._fetcher, limits=self._limits)

    def close(self) -> None:
        self._browser.close()


class PlaywrightBrowserRuntime:
    def __init__(self, *, limits: ExecutionLimits, fetcher: Any | None = None) -> None:
        self._limits = limits
        self._fetcher = fetcher or SafeHTTPSFetcher()

    def launch(self, *, headless: bool) -> _ReviewedBrowser:
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch(headless=headless)
        except BaseException:
            playwright.stop()
            raise
        return _ReviewedBrowser(
            browser, playwright, fetcher=self._fetcher, limits=self._limits
        )

    def reap(self, browser: _ReviewedBrowser) -> None:
        browser._playwright.stop()
