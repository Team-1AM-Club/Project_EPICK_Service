"""Fail-closed static response collection in one disposable Scrapy child."""

from __future__ import annotations

import codecs
import html
import logging
import math
import multiprocessing
import re
import socket
import time
from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol, cast
from urllib.parse import urljoin
from uuid import UUID

from epick_engine.source_collection.contracts import Permission
from epick_engine.source_collection.policy import (
    ExecutionLimits,
    ExecutionPolicyUnconfigured,
    PolicyBlocked,
    PolicyOperation,
    PolicySnapshot,
    Representation,
    UnsafeDestination,
    UnsupportedFormat,
    UntrustedDocument,
    ValidatedTarget,
    authorize_operation,
    authorize_robots_access,
    parse_execution_limits,
    validate_connection_destination,
    validate_redirect,
    validate_representation,
    validate_url,
)

_RETRY_HTTP_CODES = [500, 502, 503, 504, 522, 524, 408]
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_PASSWORD_INPUT = re.compile(
    r"""<input\b[^>]*\btype\s*=\s*["']?password\b""",
    re.IGNORECASE,
)
_ERROR_TITLE = re.compile(
    r"""<title\b[^>]*>\s*(?:error|access denied|forbidden|not found|server error)\b""",
    re.IGNORECASE,
)
_ERROR_HEADING = re.compile(
    r"<h[1-3]\b[^>]*>\s*(?:error|access denied|forbidden|not found|server error)(?:\b|:)",
    re.IGNORECASE,
)
_LOGIN_FORM = re.compile(
    r"<form\b[^>]*(?:action|id|name)\s*=\s*[\"'][^\"']*(?:login|sign[-_ ]?in)",
    re.IGNORECASE,
)
_SCRIPT_OR_STYLE = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG = re.compile(r"<[^>]+>")
_CHARSET = re.compile(r"charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)", re.IGNORECASE)
_ALLOWED_BODY_ENCODINGS = frozenset(
    {
        "ascii",
        "big5",
        "big5hkscs",
        "cp874",
        "cp932",
        "cp949",
        "cp1250",
        "cp1251",
        "cp1252",
        "cp1253",
        "cp1254",
        "cp1255",
        "cp1256",
        "cp1257",
        "cp1258",
        "euc_jp",
        "euc_kr",
        "gb18030",
        "gb2312",
        "gbk",
        "iso2022_jp",
        "iso2022_kr",
        "iso8859-1",
        "iso8859-2",
        "iso8859-3",
        "iso8859-4",
        "iso8859-5",
        "iso8859-6",
        "iso8859-7",
        "iso8859-8",
        "iso8859-9",
        "iso8859-10",
        "iso8859-11",
        "iso8859-13",
        "iso8859-14",
        "iso8859-15",
        "iso8859-16",
        "shift_jis",
        "utf-8",
        "utf-8-sig",
        "utf-16",
        "utf-16-be",
        "utf-16-le",
        "utf-32",
        "utf-32-be",
        "utf-32-le",
    }
)


class StaticFetchFailureCode(StrEnum):
    SOURCE_POLICY_BLOCKED = "SOURCE_POLICY_BLOCKED"
    UNSAFE_DESTINATION = "UNSAFE_DESTINATION"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    RATE_LIMITED = "RATE_LIMITED"
    ACCESS_DENIED = "ACCESS_DENIED"
    NOT_FOUND = "NOT_FOUND"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    FETCH_TIMEOUT = "FETCH_TIMEOUT"
    CANCELLED = "CANCELLED"
    FETCH_FAILED = "FETCH_FAILED"


@dataclass(frozen=True, slots=True)
class StaticFetchRequest:
    command_id: UUID
    source_url: str
    policy: PolicySnapshot
    robots_permission: Permission
    limits: ExecutionLimits
    redirect_robots_permissions: tuple[tuple[str, Permission], ...] = ()

    def __post_init__(self) -> None:
        if not self.source_url.strip():
            raise ValueError("source_url is required")
        limits = parse_execution_limits(asdict(self.limits))
        _hard_deadline_seconds(limits)
        object.__setattr__(self, "limits", limits)
        permissions = tuple(self.redirect_robots_permissions)
        if any(not url.strip() for url, _permission in permissions):
            raise ValueError("redirect robots URL is required")
        if len({url for url, _permission in permissions}) != len(permissions):
            raise ValueError("redirect robots URLs must be unique")
        object.__setattr__(self, "redirect_robots_permissions", permissions)


@dataclass(frozen=True, slots=True)
class StaticResponseSnapshot:
    url: str
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    peer_address: str | None
    raw_size: int

    def __post_init__(self) -> None:
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int):
            raise ValueError("status_code must be an integer")
        if (
            isinstance(self.raw_size, bool)
            or not isinstance(self.raw_size, int)
            or self.raw_size < 0
        ):
            raise ValueError("raw_size must be non-negative")


@dataclass(frozen=True, slots=True)
class StaticResponseCandidate:
    final_target: ValidatedTarget
    representation: Representation
    document: UntrustedDocument
    http_status: int
    raw_size: int
    decompressed_size: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.http_status, bool)
            or not isinstance(self.http_status, int)
            or not 200 <= self.http_status < 300
        ):
            raise ValueError("candidate status must be a successful HTTP status")
        for value in (self.raw_size, self.decompressed_size):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("candidate sizes must be non-negative integers")


@dataclass(frozen=True, slots=True)
class StaticFetchResult:
    command_id: UUID
    candidate: StaticResponseCandidate | None
    failure_code: StaticFetchFailureCode | None
    retry_after: str | None = None

    def __post_init__(self) -> None:
        if (self.candidate is None) == (self.failure_code is None):
            raise ValueError("exactly one candidate or failure_code is required")
        if self.candidate is not None and not isinstance(self.candidate, StaticResponseCandidate):
            raise ValueError("candidate has an invalid type")
        if self.failure_code is not None and not isinstance(
            self.failure_code, StaticFetchFailureCode
        ):
            raise ValueError("failure_code has an invalid type")


@dataclass(frozen=True, slots=True)
class _StaticRedirect:
    target: ValidatedTarget
    redirect_hops: int


type StaticResponseDecision = StaticFetchResult | _StaticRedirect
type Resolver = Callable[[str], Iterable[str]]


class _Connection(Protocol):
    def close(self) -> None: ...

    def poll(self, timeout: float = 0.0) -> bool: ...

    def recv(self) -> object: ...

    def send(self, value: object) -> None: ...


class _Process(Protocol):
    def start(self) -> None: ...

    def is_alive(self) -> bool: ...

    def join(self, timeout: float | None = None) -> None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class _ProcessContext(Protocol):
    def Pipe(self, duplex: bool = True) -> tuple[_Connection, _Connection]: ...

    def Process(
        self,
        *,
        target: Callable[..., None],
        args: tuple[object, ...],
        daemon: bool,
    ) -> _Process: ...


def _system_resolver(hostname: str) -> tuple[str, ...]:
    return tuple(
        cast(str, item[4][0])
        for item in socket.getaddrinfo(
            hostname,
            None,
            type=socket.SOCK_STREAM,
        )
    )


def _prepare_initial_target(
    request: StaticFetchRequest,
    resolver: Resolver,
) -> ValidatedTarget:
    authorize_operation(request.policy, PolicyOperation.FETCH)
    authorize_robots_access(request.robots_permission)
    return validate_url(request.source_url, resolver)


def _collector_settings(limits: ExecutionLimits) -> dict[str, object]:
    strict_timeout = min(
        limits.connect_timeout_seconds,
        limits.read_timeout_seconds,
    )
    return {
        "COMPRESSION_ENABLED": True,
        "COOKIES_ENABLED": False,
        "CONCURRENT_REQUESTS": limits.global_concurrency,
        "CONCURRENT_REQUESTS_PER_DOMAIN": limits.site_concurrency,
        "DOWNLOAD_FAIL_ON_DATALOSS": True,
        "DOWNLOAD_MAXSIZE": limits.max_response_bytes,
        "DOWNLOAD_TIMEOUT": strict_timeout,
        "DOWNLOADER_CLIENTCONTEXTFACTORY": (
            "scrapy.core.downloader.contextfactory.BrowserLikeContextFactory"
        ),
        "DOWNLOADER_MIDDLEWARES": {
            ("epick_engine.source_collection.collector.StaticDestinationValidationMiddleware"): 650,
            ("epick_engine.source_collection.collector.StaticLimitHandoffMiddleware"): 591,
        },
        "HTTPERROR_ALLOW_ALL": True,
        "HTTPPROXY_ENABLED": False,
        "LOG_ENABLED": False,
        "METAREFRESH_ENABLED": False,
        "REDIRECT_ENABLED": False,
        "RETRY_ENABLED": True,
        "RETRY_HTTP_CODES": list(_RETRY_HTTP_CODES),
        "RETRY_TIMES": limits.general_retry_limit,
        "REMOTE_CONTROL_ENABLED": False,
        "ROBOTSTXT_OBEY": False,
        "STATS_DUMP": False,
        "TELNETCONSOLE_ENABLED": False,
    }


class StaticDestinationValidationMiddleware:
    """Revalidate DNS immediately before every initial, retry, or redirect request."""

    def process_request(self, request: Any, spider: Any) -> None:
        del spider
        request.meta["static_target"] = validate_url(request.url, _system_resolver)

    def process_response(self, request: Any, response: Any, spider: Any) -> Any:
        del spider
        target = request.meta.get("static_target")
        peer = response.ip_address
        if not isinstance(target, ValidatedTarget) or peer is None:
            raise UnsafeDestination("response connection destination is unavailable")
        validate_connection_destination(target, str(peer))
        return response


class StaticLimitHandoffMiddleware:
    """Give HTTP11 and HttpCompression independent byte caps."""

    def process_request(self, request: Any, spider: Any) -> None:
        del spider
        request.meta["download_maxsize"] = request.meta["static_raw_maxsize"]

    def process_response(self, request: Any, response: Any, spider: Any) -> Any:
        del spider
        request.meta["static_raw_size"] = len(response.body)
        request.meta["download_maxsize"] = request.meta["static_decompressed_maxsize"]
        return response


def _failure(
    request: StaticFetchRequest,
    code: StaticFetchFailureCode,
    *,
    retry_after: str | None = None,
) -> StaticFetchResult:
    return StaticFetchResult(
        command_id=request.command_id,
        candidate=None,
        failure_code=code,
        retry_after=retry_after,
    )


def _evaluate_static_response(
    request: StaticFetchRequest,
    snapshot: StaticResponseSnapshot,
    target: ValidatedTarget,
    redirect_hops: int,
    resolver: Resolver,
) -> StaticResponseDecision:
    if snapshot.raw_size > request.limits.max_response_bytes:
        return _failure(request, StaticFetchFailureCode.RESPONSE_TOO_LARGE)
    if len(snapshot.body) > request.limits.max_decompressed_bytes:
        return _failure(request, StaticFetchFailureCode.RESPONSE_TOO_LARGE)
    if snapshot.peer_address is None:
        return _failure(request, StaticFetchFailureCode.UNSAFE_DESTINATION)
    try:
        validate_connection_destination(target, snapshot.peer_address)
    except UnsafeDestination:
        return _failure(request, StaticFetchFailureCode.UNSAFE_DESTINATION)

    if snapshot.status_code in _REDIRECT_STATUSES:
        location = _header(snapshot.headers, "location")
        if location is None or redirect_hops >= request.limits.max_redirects:
            return _failure(request, StaticFetchFailureCode.UNSAFE_DESTINATION)
        redirect_url = urljoin(snapshot.url, location)
        robots_permission = next(
            (
                permission
                for permitted_url, permission in request.redirect_robots_permissions
                if permitted_url == redirect_url
            ),
            Permission.UNKNOWN,
        )
        try:
            authorize_robots_access(robots_permission)
        except PolicyBlocked:
            return _failure(request, StaticFetchFailureCode.SOURCE_POLICY_BLOCKED)
        try:
            redirected = validate_redirect(
                snapshot.url,
                location,
                resolver,
            )
        except UnsafeDestination:
            return _failure(request, StaticFetchFailureCode.UNSAFE_DESTINATION)
        return _StaticRedirect(redirected, redirect_hops + 1)

    if snapshot.status_code == 429:
        return _failure(
            request,
            StaticFetchFailureCode.RATE_LIMITED,
            retry_after=_header(snapshot.headers, "retry-after"),
        )
    if snapshot.status_code in {401, 403}:
        return _failure(request, StaticFetchFailureCode.ACCESS_DENIED)
    if snapshot.status_code == 404:
        return _failure(request, StaticFetchFailureCode.NOT_FOUND)
    if snapshot.status_code < 200 or snapshot.status_code >= 300:
        return _failure(request, StaticFetchFailureCode.FETCH_FAILED)

    content_type = _header(snapshot.headers, "content-type") or ""
    if b"%PDF-" in snapshot.body[:1024]:
        return _failure(request, StaticFetchFailureCode.UNSUPPORTED_FORMAT)
    try:
        representation = validate_representation(snapshot.url, content_type)
    except UnsupportedFormat:
        return _failure(request, StaticFetchFailureCode.UNSUPPORTED_FORMAT)
    try:
        text = _decode_body(snapshot.body, content_type)
    except UnicodeError:
        return _failure(request, StaticFetchFailureCode.EXTRACTION_FAILED)
    document = UntrustedDocument(text)
    if _is_rejected_document(document.text, representation):
        return _failure(request, StaticFetchFailureCode.EXTRACTION_FAILED)
    return StaticFetchResult(
        command_id=request.command_id,
        candidate=StaticResponseCandidate(
            final_target=target,
            representation=representation,
            document=document,
            http_status=snapshot.status_code,
            raw_size=snapshot.raw_size,
            decompressed_size=len(snapshot.body),
        ),
        failure_code=None,
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    normalized = name.casefold()
    return next(
        (value for key, value in headers.items() if key.casefold() == normalized),
        None,
    )


def _decode_body(body: bytes, content_type: str) -> str:
    match = _CHARSET.search(content_type)
    declared_encoding = match.group(1) if match is not None else "utf-8"
    try:
        resolved_encoding = codecs.lookup(declared_encoding).name
    except LookupError:
        resolved_encoding = "utf-8"
    encoding = resolved_encoding if resolved_encoding in _ALLOWED_BODY_ENCODINGS else "utf-8"
    text = body.decode(encoding, errors="replace")
    text.encode()
    return text


def _is_rejected_html(text: str) -> bool:
    if (
        _PASSWORD_INPUT.search(text)
        or _LOGIN_FORM.search(text)
        or _ERROR_TITLE.search(text)
        or _ERROR_HEADING.search(text)
    ):
        return True
    visible = _SCRIPT_OR_STYLE.sub(" ", text)
    visible = html.unescape(_HTML_TAG.sub(" ", visible))
    normalized = " ".join(visible.split()).casefold()
    return not normalized or normalized in {
        "access denied",
        "error",
        "forbidden",
        "not found",
        "server error",
    }


def _is_rejected_document(text: str, representation: Representation) -> bool:
    return (
        not text.strip()
        or "%PDF-" in text[:1024]
        or (representation is Representation.HTML and _is_rejected_html(text))
    )


def _request_meta(
    request: StaticFetchRequest,
    target: ValidatedTarget,
    redirect_hops: int,
) -> dict[str, object]:
    return {
        "download_maxsize": request.limits.max_response_bytes,
        "download_timeout": min(
            request.limits.connect_timeout_seconds,
            request.limits.read_timeout_seconds,
        ),
        "handle_httpstatus_all": True,
        "static_decompressed_maxsize": request.limits.max_decompressed_bytes,
        "static_raw_maxsize": request.limits.max_response_bytes,
        "static_redirect_hops": redirect_hops,
        "static_target": target,
    }


def _response_snapshot(response: Any) -> StaticResponseSnapshot:
    headers: dict[str, str] = {}
    for raw_name, raw_values in response.headers.items():
        raw_value = raw_values[-1] if isinstance(raw_values, list) else raw_values
        name = raw_name.decode("latin-1") if isinstance(raw_name, bytes) else str(raw_name)
        value = raw_value.decode("latin-1") if isinstance(raw_value, bytes) else str(raw_value)
        headers[name] = value
    peer = response.ip_address
    return StaticResponseSnapshot(
        url=response.url,
        status_code=response.status,
        headers=headers,
        body=bytes(response.body),
        peer_address=str(peer) if peer is not None else None,
        raw_size=int(response.meta.get("static_raw_size", len(response.body))),
    )


def _exception_failure_code(exc: object) -> StaticFetchFailureCode:
    if isinstance(exc, PolicyBlocked):
        return StaticFetchFailureCode.SOURCE_POLICY_BLOCKED
    if isinstance(exc, UnsafeDestination):
        return StaticFetchFailureCode.UNSAFE_DESTINATION
    if isinstance(exc, UnsupportedFormat):
        return StaticFetchFailureCode.UNSUPPORTED_FORMAT
    name = type(exc).__name__.casefold()
    if name == "ignorerequest":
        return StaticFetchFailureCode.RESPONSE_TOO_LARGE
    if "timeout" in name or "cancel" in name:
        return StaticFetchFailureCode.FETCH_TIMEOUT
    if "toomanyredirect" in name or "downloaderr" in name:
        return StaticFetchFailureCode.RESPONSE_TOO_LARGE
    return StaticFetchFailureCode.FETCH_FAILED


def _run_static_child(
    input_connection: _Connection,
    output_connection: _Connection,
) -> None:
    """Top-level spawn target. Sensitive fetch input arrives only after start."""

    logging.disable(logging.CRITICAL)
    request: StaticFetchRequest | None = None
    sent = False

    def emit(result: StaticFetchResult) -> None:
        nonlocal sent
        if not sent:
            output_connection.send(result)
            sent = True

    try:
        raw_request = input_connection.recv()
        if not isinstance(raw_request, StaticFetchRequest):
            return
        request = raw_request
        fetch_request = raw_request
        initial_target = _prepare_initial_target(fetch_request, _system_resolver)

        from scrapy import Request, Spider
        from scrapy.crawler import CrawlerProcess

        class _StaticSpider(Spider):
            name = "epick_static_collector"

            async def start(self) -> AsyncIterator[Request]:
                yield Request(
                    initial_target.url,
                    callback=self.parse_response,
                    errback=self.failed,
                    dont_filter=True,
                    meta=_request_meta(fetch_request, initial_target, 0),
                )

            def parse_response(self, response: Any) -> Iterator[Request]:
                snapshot = _response_snapshot(response)
                target = cast(ValidatedTarget, response.meta["static_target"])
                hops = int(response.meta["static_redirect_hops"])
                decision = _evaluate_static_response(
                    fetch_request,
                    snapshot,
                    target,
                    hops,
                    _system_resolver,
                )
                if isinstance(decision, StaticFetchResult):
                    emit(decision)
                    return
                yield Request(
                    decision.target.url,
                    callback=self.parse_response,
                    errback=self.failed,
                    dont_filter=True,
                    meta=_request_meta(
                        fetch_request,
                        decision.target,
                        decision.redirect_hops,
                    ),
                )

            def failed(self, failure: Any) -> None:
                emit(_failure(fetch_request, _exception_failure_code(failure.value)))

        crawler = CrawlerProcess(
            settings=_collector_settings(fetch_request.limits),
            install_root_handler=False,
        )
        crawler.crawl(_StaticSpider)
        crawler.start(stop_after_crawl=True, install_signal_handlers=False)
        if not sent:
            emit(_failure(fetch_request, StaticFetchFailureCode.FETCH_FAILED))
    except Exception as exc:
        if request is not None:
            emit(_failure(request, _exception_failure_code(exc)))
    finally:
        input_connection.close()
        output_connection.close()


def _hard_deadline_seconds(limits: ExecutionLimits) -> float:
    per_attempt = min(
        limits.connect_timeout_seconds,
        limits.read_timeout_seconds,
    )
    try:
        deadline = per_attempt * (limits.general_retry_limit + 1) * (limits.max_redirects + 1)
    except OverflowError as exc:
        raise ExecutionPolicyUnconfigured("collector hard deadline must be finite") from exc
    if not math.isfinite(deadline) or deadline <= 0:
        raise ExecutionPolicyUnconfigured("collector hard deadline must be positive and finite")
    return deadline


class StaticScrapyCollector:
    """Run exactly one Scrapy crawl per disposable spawn child."""

    def __init__(
        self,
        *,
        process_context: _ProcessContext | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        poll_interval_seconds: float = 0.05,
        reap_timeout_seconds: float = 1.0,
    ) -> None:
        if poll_interval_seconds <= 0 or reap_timeout_seconds <= 0:
            raise ValueError("collector polling and reap timeouts must be positive")
        self._context = process_context or cast(
            _ProcessContext,
            multiprocessing.get_context("spawn"),
        )
        self._monotonic = monotonic
        self._poll_interval_seconds = poll_interval_seconds
        self._reap_timeout_seconds = reap_timeout_seconds
        self._active_process: _Process | None = None
        self._active_started = False
        self._closed = False

    def fetch(
        self,
        request: StaticFetchRequest,
        *,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> StaticFetchResult:
        if self._closed:
            raise RuntimeError("collector is closed")
        if self._active_process is not None:
            raise RuntimeError("collector already has an active child")

        try:
            deadline = self._monotonic() + _hard_deadline_seconds(request.limits)
            if not math.isfinite(deadline):
                raise ExecutionPolicyUnconfigured("collector absolute deadline must be finite")
        except (ArithmeticError, ExecutionPolicyUnconfigured, TypeError, ValueError):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)

        child_input, parent_input = self._context.Pipe(duplex=False)
        parent_output, child_output = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_run_static_child,
            args=(child_input, child_output),
            daemon=False,
        )
        self._active_process = process
        try:
            process.start()
            self._active_started = True
            child_input.close()
            child_output.close()
            parent_input.send(request)
            parent_input.close()
            while True:
                if is_cancelled():
                    self._stop_active()
                    return _failure(request, StaticFetchFailureCode.CANCELLED)
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    self._stop_active()
                    return _failure(request, StaticFetchFailureCode.FETCH_TIMEOUT)
                if parent_output.poll(min(self._poll_interval_seconds, remaining)):
                    return self._validated_child_result(request, parent_output.recv())
                if not process.is_alive():
                    return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        except (EOFError, OSError, ValueError):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        finally:
            parent_input.close()
            parent_output.close()
            self._reap_active()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_active()

    def _validated_child_result(
        self,
        request: StaticFetchRequest,
        raw_result: object,
    ) -> StaticFetchResult:
        if not isinstance(raw_result, StaticFetchResult):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        if raw_result.command_id != request.command_id:
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        candidate: object = raw_result.candidate
        failure_code: object = raw_result.failure_code
        retry_after: object = raw_result.retry_after
        if (candidate is None) == (failure_code is None):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        if retry_after is not None and not isinstance(retry_after, str):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        if candidate is None:
            if not isinstance(failure_code, StaticFetchFailureCode):
                return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
            return raw_result
        if retry_after is not None or not isinstance(candidate, StaticResponseCandidate):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        target = candidate.final_target
        document = candidate.document
        if (
            not isinstance(target, ValidatedTarget)
            or not isinstance(target.url, str)
            or not target.url
            or not isinstance(target.hostname, str)
            or not target.hostname
            or isinstance(target.port, bool)
            or not isinstance(target.port, int)
            or not 1 <= target.port <= 65535
            or not isinstance(target.resolved_addresses, frozenset)
            or not target.resolved_addresses
            or not all(
                isinstance(address, str) and address for address in target.resolved_addresses
            )
            or not isinstance(candidate.representation, Representation)
            or not isinstance(document, UntrustedDocument)
            or not isinstance(document.text, str)
            or isinstance(candidate.http_status, bool)
            or not isinstance(candidate.http_status, int)
            or not 200 <= candidate.http_status < 300
        ):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        try:
            revalidated_target = validate_url(
                target.url,
                lambda _hostname: target.resolved_addresses,
            )
        except (TypeError, UnsafeDestination, ValueError):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        if revalidated_target != target or _is_rejected_document(
            document.text,
            candidate.representation,
        ):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        sizes = (candidate.raw_size, candidate.decompressed_size)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in sizes
        ):
            return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
        if (
            candidate.raw_size > request.limits.max_response_bytes
            or candidate.decompressed_size > request.limits.max_decompressed_bytes
            or len(document.text.encode("utf-8")) > request.limits.max_decompressed_bytes
        ):
            return _failure(request, StaticFetchFailureCode.RESPONSE_TOO_LARGE)
        return raw_result

    def _stop_active(self) -> None:
        process = self._active_process
        if process is None or not self._active_started:
            return
        if not process.is_alive():
            process.join(0.0)
            return
        process.terminate()
        process.join(self._reap_timeout_seconds)
        if process.is_alive():
            process.kill()
            process.join(self._reap_timeout_seconds)

    def _reap_active(self) -> None:
        process = self._active_process
        if process is None:
            return
        if self._active_started:
            process.join(self._reap_timeout_seconds)
        if process.is_alive():
            self._stop_active()
        self._active_process = None
        self._active_started = False


class RenderedCollector:
    """Fetch one explicitly approved JS page through an isolated browser runtime.

    The runtime must expose connection peer addresses *before* continuing each
    request. A browser adapter lacking that capability fails closed; URL-only
    route checks are insufficient against DNS rebinding and private egress.
    """

    def __init__(
        self,
        *,
        browser_runtime: Any,
        approved_js_urls: frozenset[str],
        resolver: Resolver = _system_resolver,
    ) -> None:
        self._runtime = browser_runtime
        self._approved_js_urls = frozenset(approved_js_urls)
        self._resolver = resolver
        self._closed = False

    def fetch(
        self,
        request: StaticFetchRequest,
        *,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> StaticFetchResult:
        if self._closed:
            raise RuntimeError("collector is closed")
        try:
            authorize_operation(request.policy, PolicyOperation.FETCH)
            authorize_robots_access(request.robots_permission)
        except PolicyBlocked:
            return _failure(request, StaticFetchFailureCode.SOURCE_POLICY_BLOCKED)
        if request.source_url not in self._approved_js_urls:
            return _failure(request, StaticFetchFailureCode.SOURCE_POLICY_BLOCKED)
        if is_cancelled():
            return _failure(request, StaticFetchFailureCode.CANCELLED)

        browser: Any = None
        context: Any = None
        page: Any = None
        blocked: StaticFetchFailureCode | None = None
        routed_targets: dict[str, ValidatedTarget] = {}

        def route_request(route: Any) -> None:
            nonlocal blocked
            routed_request = getattr(route, "request", None)
            url = getattr(routed_request, "url", None)
            peer = getattr(routed_request, "peer_address", None)
            fetch_failure = getattr(routed_request, "failure_code", None)
            try:
                if isinstance(fetch_failure, StaticFetchFailureCode):
                    blocked = fetch_failure
                    route.abort()
                    return
                if not isinstance(url, str) or not isinstance(peer, str):
                    raise UnsafeDestination("browser peer is unavailable before request")
                target = validate_url(url, self._resolver)
                validate_connection_destination(target, peer)
                routed_targets[url] = target
                route.continue_()
            except (TypeError, UnsafeDestination, ValueError):
                blocked = StaticFetchFailureCode.UNSAFE_DESTINATION
                route.abort()

        try:
            browser = self._runtime.launch(headless=True)
            context = browser.new_context(service_workers="block", accept_downloads=False)
            page = context.new_page()
            page.route("**/*", route_request)
            try:
                response = page.goto(request.source_url)
            except Exception as exc:
                return _failure(request, blocked or _exception_failure_code(exc))
            if blocked is not None:
                return _failure(request, blocked)
            if is_cancelled():
                return _failure(request, StaticFetchFailureCode.CANCELLED)

            url = getattr(response, "url", None)
            status = getattr(response, "status", None)
            headers = getattr(response, "headers", None)
            peer = getattr(response, "peer_address", None)
            if (
                not isinstance(url, str)
                or isinstance(status, bool)
                or not isinstance(status, int)
                or not isinstance(headers, Mapping)
                or not isinstance(peer, str)
            ):
                return _failure(request, StaticFetchFailureCode.FETCH_FAILED)
            target = routed_targets.get(url)
            if target is None:
                try:
                    target = validate_url(url, self._resolver)
                except (TypeError, UnsafeDestination, ValueError):
                    return _failure(request, StaticFetchFailureCode.UNSAFE_DESTINATION)
            try:
                validate_connection_destination(target, peer)
            except UnsafeDestination:
                return _failure(request, StaticFetchFailureCode.UNSAFE_DESTINATION)
            if status == 429:
                return _failure(
                    request,
                    StaticFetchFailureCode.RATE_LIMITED,
                    retry_after=_header(headers, "retry-after"),
                )
            if status in (401, 403):
                return _failure(request, StaticFetchFailureCode.ACCESS_DENIED)
            if status == 404:
                return _failure(request, StaticFetchFailureCode.NOT_FOUND)
            if not 200 <= status < 300:
                return _failure(request, StaticFetchFailureCode.FETCH_FAILED)

            rendered = page.content()
            if not isinstance(rendered, str):
                return _failure(request, StaticFetchFailureCode.EXTRACTION_FAILED)
            encoded = rendered.encode("utf-8")
            if len(encoded) > min(
                request.limits.max_response_bytes, request.limits.max_decompressed_bytes
            ):
                return _failure(request, StaticFetchFailureCode.RESPONSE_TOO_LARGE)
            if _is_rejected_document(rendered, Representation.HTML):
                return _failure(request, StaticFetchFailureCode.EXTRACTION_FAILED)
            return StaticFetchResult(
                command_id=request.command_id,
                candidate=StaticResponseCandidate(
                    final_target=target,
                    representation=Representation.HTML,
                    document=UntrustedDocument(rendered),
                    http_status=status,
                    raw_size=len(encoded),
                    decompressed_size=len(encoded),
                ),
                failure_code=None,
            )
        except Exception as exc:
            return _failure(request, _exception_failure_code(exc))
        finally:
            for resource in (page, context, browser):
                close = getattr(resource, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            if browser is not None:
                reap = getattr(self._runtime, "reap", None)
                if callable(reap):
                    try:
                        reap(browser)
                    except Exception:
                        pass

    def close(self) -> None:
        self._closed = True
