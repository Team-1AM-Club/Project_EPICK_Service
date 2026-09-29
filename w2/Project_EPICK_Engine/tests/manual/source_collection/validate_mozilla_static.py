from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
from pathlib import Path
from urllib.parse import urlsplit

from protego import Protego

import epick_engine.source_collection.parsing as parsing_module
from epick_engine.source_collection.collector import StaticResponseCandidate
from epick_engine.source_collection.contracts import ExtractionStatus, PostingSectionKind
from epick_engine.source_collection.parsing import (
    extract_static_candidate,
    parse_approved_static_posting,
    validate_evidence_locator,
)
from epick_engine.source_collection.policy import (
    Representation,
    UntrustedDocument,
    ValidatedTarget,
)

ROOT = Path(__file__).resolve().parents[3]
if not Path(parsing_module.__file__).resolve().is_relative_to(ROOT / "src"):
    raise RuntimeError("production parser origin mismatch")

HOST = "www.mozilla.org"
ROBOTS_URL = f"https://{HOST}/robots.txt"
JOB_URL = f"https://{HOST}/en-US/careers/position/gh/8035891/"
USER_AGENT = "Scrapy/2.18.0 (+https://scrapy.org)"
CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 10
ROBOTS_LIMIT_BYTES = 100_000
JOB_LIMIT_BYTES = 1_000_000
GENERAL_SECTION_HEADINGS = frozenset({"what you'll get", "what you’ll get"})


def resolve_public_addresses(hostname: str) -> tuple[str, ...]:
    addresses = tuple(
        sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(
                    hostname,
                    443,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
            }
        )
    )
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise RuntimeError("unsafe DNS result")
    return addresses


def fetch_once(
    url: str,
    *,
    limit: int,
) -> tuple[bytes, dict[str, object], tuple[str, ...]]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != HOST or parsed.port not in (None, 443):
        raise RuntimeError("unexpected URL")
    addresses = resolve_public_addresses(HOST)
    address = addresses[0]
    raw_socket = socket.create_connection((address, 443), timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        peer = raw_socket.getpeername()[0]
        if ipaddress.ip_address(peer) != ipaddress.ip_address(address):
            raise RuntimeError("peer mismatch")
        tls_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=HOST)
        raw_socket = None
        try:
            tls_socket.settimeout(READ_TIMEOUT_SECONDS)
            target = parsed.path or "/"
            if parsed.query:
                target = f"{target}?{parsed.query}"
            request = (
                f"GET {target} HTTP/1.1\r\n"
                f"Host: {HOST}\r\n"
                f"User-Agent: {USER_AGENT}\r\n"
                "Accept: text/html,text/plain;q=0.9\r\n"
                "Accept-Encoding: identity\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            tls_socket.sendall(request)
            response = http.client.HTTPResponse(tls_socket)
            response.begin()
            content_length_raw = response.getheader("Content-Length")
            content_encoding = (response.getheader("Content-Encoding") or "identity").lower()
            transfer_encoding = response.getheader("Transfer-Encoding")
            if response.status != 200:
                raise RuntimeError(f"unexpected HTTP status {response.status}")
            if content_encoding != "identity" or transfer_encoding is not None:
                raise RuntimeError("unsupported response encoding")
            if content_length_raw is None or not content_length_raw.isdecimal():
                raise RuntimeError("missing content length")
            content_length = int(content_length_raw)
            if content_length > limit:
                raise RuntimeError("response exceeds byte limit")
            body = response.read(limit + 1)
            if len(body) != content_length or len(body) > limit:
                raise RuntimeError("incomplete or oversized response")
            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].lower()
            return (
                body,
                {
                    "status": response.status,
                    "content_type": content_type,
                    "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "resolved_address_count": len(addresses),
                    "tls_version": tls_socket.version(),
                },
                addresses,
            )
        finally:
            tls_socket.close()
    finally:
        if raw_socket is not None:
            raw_socket.close()


def evidence_summary(document: str, semantic, kind: PostingSectionKind) -> dict[str, object]:
    evidence_by_key = {item.evidence_key: item for item in semantic_source.evidence}
    section = next(
        item
        for item in semantic.sections
        if item.kind is kind
        and (
            kind is not PostingSectionKind.GENERAL
            or (item.heading_raw or "").strip().rstrip(":").casefold() in GENERAL_SECTION_HEADINGS
        )
    )
    evidence = next(
        evidence_by_key[key]
        for key in section.evidence_keys
        if validate_evidence_locator(document, evidence_by_key[key])
    )
    return {
        "kind": kind.value,
        "locator_kind": evidence.locator.kind.value,
        "locator": evidence.locator.value,
        "excerpt_length": len(evidence.text_excerpt),
        "excerpt_sha256": hashlib.sha256(evidence.text_excerpt.encode("utf-8")).hexdigest(),
    }


robots_body, robots_metadata, _ = fetch_once(ROBOTS_URL, limit=ROBOTS_LIMIT_BYTES)
robots = Protego.parse(robots_body.decode("utf-8", errors="strict"))
if not robots.can_fetch(JOB_URL, USER_AGENT):
    raise RuntimeError("robots policy denied the approved posting")

job_body, job_metadata, job_addresses = fetch_once(JOB_URL, limit=JOB_LIMIT_BYTES)
if job_metadata["content_type"] != "text/html":
    raise RuntimeError("approved posting did not return HTML")
document = job_body.decode("utf-8", errors="strict")
target = ValidatedTarget(
    url=JOB_URL,
    hostname=HOST,
    port=443,
    resolved_addresses=frozenset(job_addresses),
)
candidate = StaticResponseCandidate(
    final_target=target,
    representation=Representation.HTML,
    document=UntrustedDocument(document),
    http_status=200,
    raw_size=len(job_body),
    decompressed_size=len(job_body),
)
semantic_source = extract_static_candidate(candidate)
semantic = parse_approved_static_posting(semantic_source)

if semantic_source.extraction_status is not ExtractionStatus.COMPLETE:
    raise RuntimeError("static extraction was not complete")
if parsing_module.PARSER_VERSION != "epick-static-evidence-v3":
    raise RuntimeError("unexpected parser version")
if semantic.job_title.status != "known" or len(semantic.job_title.evidence_keys) != 1:
    raise RuntimeError("verified H1 title was not available")
if not all(validate_evidence_locator(document, item) for item in semantic_source.evidence):
    raise RuntimeError("invalid Evidence locator")

required_kinds = (
    PostingSectionKind.TITLE,
    PostingSectionKind.DUTIES,
    PostingSectionKind.REQUIRED,
    PostingSectionKind.PREFERRED,
    PostingSectionKind.GENERAL,
)
region_counts = {
    kind.value: sum(section.kind is kind for section in semantic.sections)
    for kind in required_kinds
}
missing_regions = [kind for kind, count in region_counts.items() if count == 0]
if missing_regions:
    raise RuntimeError(f"required static regions missing: {','.join(missing_regions)}")

print(
    json.dumps(
        {
            "outcome": "pass",
            "requests": ["robots", "job"],
            "redirects": 0,
            "retries": 0,
            "robots": robots_metadata,
            "job": job_metadata,
            "parser_version": parsing_module.PARSER_VERSION,
            "extraction_status": semantic_source.extraction_status.value,
            "evidence_count": len(semantic_source.evidence),
            "all_locators_valid": True,
            "regions": [evidence_summary(document, semantic, kind) for kind in required_kinds],
            "external_subrequests": 0,
            "body_persisted": False,
        },
        ensure_ascii=True,
        sort_keys=True,
    )
)
