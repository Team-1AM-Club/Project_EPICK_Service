"""Contract tests for synthetic official page and API collection fixtures."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

import epick_engine.source_collection.collector as collector_module
from epick_engine.source_collection.collector import (
    StaticFetchFailureCode,
    StaticFetchRequest,
    StaticFetchResult,
    StaticResponseCandidate,
    StaticResponseSnapshot,
    _evaluate_static_response,
    _prepare_initial_target,
)
from epick_engine.source_collection.contracts import (
    AccessClass,
    Locator,
    LocatorKind,
    OfficialStatus,
    Permission,
)
from epick_engine.source_collection.contracts import (
    Representation as PublicRepresentation,
)
from epick_engine.source_collection.parsing import (
    EvidenceDraft,
    extract_static_candidate,
    validate_evidence_locator,
)
from epick_engine.source_collection.policy import (
    ExecutionLimits,
    PolicySnapshot,
    UnsafeDestination,
    UntrustedDocument,
)
from epick_engine.source_collection.policy import Representation as TransportRepresentation

_FIXTURE_DIRECTORY = Path(__file__).resolve().parents[2] / "fixtures" / "synthetic_sources"
_COMMAND_ID = UUID("00000000-0000-0000-0000-000000000047")
_PUBLIC_IP = "93.184.216.34"
_PUBLIC_TO_TRANSPORT_REPRESENTATION = {
    PublicRepresentation.STATIC_HTML: TransportRepresentation.HTML,
    PublicRepresentation.OFFICIAL_JSON: TransportRepresentation.JSON,
}


def _fixture(name: str) -> Mapping[str, Any]:
    return json.loads((_FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"))


def _case(fixture: Mapping[str, Any], case_id: str) -> Mapping[str, Any]:
    return next(case for case in fixture["cases"] if case["case_id"] == case_id)


def _resolver(*addresses: str) -> Callable[[str], Iterable[str]]:
    def resolve(_hostname: str) -> Iterable[str]:
        return addresses

    return resolve


def _policy() -> PolicySnapshot:
    return PolicySnapshot(
        official_status=OfficialStatus.VERIFIED,
        access_class=AccessClass.PUBLIC,
        collection_permission=Permission.ALLOWED,
        excerpt_storage_permission=Permission.ALLOWED,
        body_storage_permission=Permission.DENIED,
        redistribution_permission=Permission.DENIED,
        revision=1,
    )


def _limits() -> ExecutionLimits:
    return ExecutionLimits(
        site_concurrency=2,
        global_concurrency=8,
        source_ttl_seconds=3600,
        max_response_bytes=1_000_000,
        max_decompressed_bytes=1_000_000,
        connect_timeout_seconds=3.0,
        read_timeout_seconds=9.0,
        max_redirects=2,
        general_retry_limit=1,
        retention_days=30,
    )


def _request(source_url: str) -> StaticFetchRequest:
    return StaticFetchRequest(
        command_id=_COMMAND_ID,
        source_url=source_url,
        policy=_policy(),
        robots_permission=Permission.ALLOWED,
        limits=_limits(),
    )


def _response_decision(case: Mapping[str, Any]) -> StaticFetchResult:
    transport = case["transport"]
    request = _request(transport["request_url"])
    target = _prepare_initial_target(request, _resolver(_PUBLIC_IP))
    raw_body = transport["raw_body"]
    response_body = b"" if raw_body is None else raw_body.encode("utf-8")
    snapshot = StaticResponseSnapshot(
        url=transport["request_url"],
        status_code=transport["status"],
        headers=transport["headers"],
        body=response_body,
        peer_address=_PUBLIC_IP,
        raw_size=len(response_body),
    )
    decision = _evaluate_static_response(
        request,
        snapshot,
        target,
        0,
        _resolver(_PUBLIC_IP),
    )

    assert isinstance(decision, StaticFetchResult)
    return decision


def _expected_evidence(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return list(case["expected"]["evidence"])


def _expected_transport_representation(case: Mapping[str, Any]) -> TransportRepresentation:
    public_representation = PublicRepresentation(case["expected"]["representation"])
    return _PUBLIC_TO_TRANSPORT_REPRESENTATION[public_representation]


def _assert_fixture_section_references(case: Mapping[str, Any]) -> None:
    expected = case["expected"]
    evidence = _expected_evidence(case)
    evidence_refs = {item["ref"] for item in evidence}

    assert len(evidence_refs) == len(evidence)
    assert [item["chunk_order"] for item in evidence] == list(range(len(evidence)))
    for section in expected["sections"]:
        assert section["evidence_refs"]
        assert set(section["evidence_refs"]) <= evidence_refs


def _assert_expected_evidence_subset(
    parsed: Any,
    case: Mapping[str, Any],
) -> None:
    expected = [
        (
            evidence["text_excerpt"],
            evidence["locator"]["kind"],
            evidence["locator"]["value"],
        )
        for evidence in _expected_evidence(case)
    ]
    actual = [
        (
            evidence.text_excerpt,
            evidence.locator.kind.value,
            evidence.locator.value,
        )
        for evidence in parsed.evidence
    ]

    positions = []
    for item in expected:
        assert item in actual
        positions.append(actual.index(item))
    assert positions == sorted(positions)


def _assert_expected_sections(parsed: Any, case: Mapping[str, Any]) -> None:
    evidence_key_by_ref: dict[str, str] = {}
    for expected in _expected_evidence(case):
        matches = [
            evidence
            for evidence in parsed.evidence
            if (
                evidence.text_excerpt,
                evidence.locator.kind.value,
                evidence.locator.value,
            )
            == (
                expected["text_excerpt"],
                expected["locator"]["kind"],
                expected["locator"]["value"],
            )
        ]
        assert len(matches) == 1
        evidence_key_by_ref[expected["ref"]] = matches[0].evidence_key

    section_orders: list[int] = []
    for expected in case["expected"]["sections"]:
        expected_keys = tuple(
            evidence_key_by_ref[reference] for reference in expected["evidence_refs"]
        )
        matches = [
            section
            for section in parsed.sections
            if section.heading_raw == expected["heading"] and section.evidence_keys == expected_keys
        ]
        assert len(matches) == 1
        section_orders.append(matches[0].order)
    assert section_orders == sorted(section_orders)


def _assert_published_at(parsed: Any, expected: Mapping[str, Any]) -> None:
    published_at = expected["published_at"]

    assert parsed.published_at.status.value == published_at["status"]
    assert parsed.published_at.value == published_at["value"]
    assert (
        parsed.published_at.precision.value if parsed.published_at.precision is not None else None
    ) == (published_at.get("precision"))
    assert parsed.published_at.raw_text == published_at.get("raw_text")
    assert parsed.published_at.timezone == published_at.get("timezone")


@pytest.mark.parametrize("fixture_name", ["official_pages.json", "official_api.json"])
def test_fixture_schema_and_expected_section_references_are_consistent(
    fixture_name: str,
) -> None:
    fixture = _fixture(fixture_name)

    assert fixture["schema_version"] == "1.0"
    assert fixture["synthetic"] is True
    assert fixture["description"]
    assert len({case["case_id"] for case in fixture["cases"]}) == len(fixture["cases"])
    for case in fixture["cases"]:
        assert case["description"]
        assert case["transport"]
        assert case["expected"]
        _assert_fixture_section_references(case)


@pytest.mark.parametrize(
    "case_id",
    [
        "official_page_homepage_static",
        "official_page_press_release_static",
        "official_page_executive_message_static",
        "official_page_ir_disclosure_static",
    ],
)
def test_official_html_categories_keep_expected_evidence_and_dates(
    case_id: str,
) -> None:
    case = _case(_fixture("official_pages.json"), case_id)
    decision = _response_decision(case)

    assert decision.failure_code is None
    assert decision.candidate is not None
    assert decision.candidate.representation is _expected_transport_representation(case)
    parsed = extract_static_candidate(decision.candidate)

    assert parsed.representation is _expected_transport_representation(case)
    assert parsed.extraction_status.value == case["expected"]["extraction"]
    _assert_expected_evidence_subset(parsed, case)
    _assert_published_at(parsed, case["expected"])


@pytest.mark.parametrize(
    "case_id, expected_status",
    [
        ("official_api_direct_json_ordered", "complete"),
        ("official_api_200_partial", "partial"),
    ],
)
def test_direct_json_preserves_fixture_pointer_and_lexical_order(
    case_id: str,
    expected_status: str,
) -> None:
    case = _case(_fixture("official_api.json"), case_id)
    decision = _response_decision(case)

    assert decision.failure_code is None
    assert decision.candidate is not None
    assert decision.candidate.representation is _expected_transport_representation(case)
    assert decision.candidate.http_status == case["transport"]["status"]
    parsed = extract_static_candidate(decision.candidate)

    assert parsed.representation is _expected_transport_representation(case)
    assert parsed.extraction_status.value == expected_status
    _assert_expected_evidence_subset(parsed, case)
    _assert_expected_sections(parsed, case)
    _assert_published_at(parsed, case["expected"])
    expected_texts = [item["text_excerpt"] for item in _expected_evidence(case)]
    raw_body = case["transport"]["raw_body"]
    positions = []
    cursor = 0
    for text in expected_texts:
        position = raw_body.find(text, cursor)
        assert position >= 0
        positions.append(position)
        cursor = position + len(text)
    assert positions == sorted(positions)


def test_malformed_json_is_a_failed_json_parse_without_evidence() -> None:
    case = _case(_fixture("official_api.json"), "official_api_malformed_json")
    decision = _response_decision(case)

    assert decision.failure_code is None
    assert decision.candidate is not None
    assert decision.candidate.representation is _expected_transport_representation(case)
    assert decision.candidate.http_status == case["transport"]["status"]
    parsed = extract_static_candidate(decision.candidate)

    assert parsed.extraction_status.value == "failed"
    assert parsed.evidence == ()
    assert parsed.sections == ()
    assert "malformed_json" in parsed.limitations


def test_embedded_json_uses_pointer_evidence_without_promoting_script_body() -> None:
    case = _case(_fixture("official_api.json"), "official_api_embedded_json_html")
    decision = _response_decision(case)

    assert decision.failure_code is None
    assert decision.candidate is not None
    assert decision.candidate.representation is _expected_transport_representation(case)
    parsed = extract_static_candidate(decision.candidate)

    assert parsed.extraction_status.value == "complete"
    _assert_expected_evidence_subset(parsed, case)
    _assert_expected_sections(parsed, case)
    assert case["transport"]["embedded_json_raw_body"] not in [
        evidence.text_excerpt for evidence in parsed.evidence
    ]
    _assert_published_at(parsed, case["expected"])


def test_published_time_ignores_deadline_time() -> None:
    document = """<html><body>
    <h2>Published</h2>
    <time datetime="2031-05-14">May 14, 2031</time>
    <h2>Application deadline</h2>
    <time datetime="2031-06-01">June 1, 2031</time>
    </body></html>"""
    request = _request("https://synthetic-meridian.example.test/careers/role")
    document_size = len(document.encode("utf-8"))
    candidate = StaticResponseCandidate(
        final_target=_prepare_initial_target(request, _resolver(_PUBLIC_IP)),
        representation=TransportRepresentation.HTML,
        document=UntrustedDocument(document),
        http_status=200,
        raw_size=document_size,
        decompressed_size=document_size,
    )

    parsed = extract_static_candidate(candidate)

    assert parsed.published_at.status.value == "known"
    assert parsed.published_at.value == "2031-05-14"
    assert parsed.published_at.precision.value == "date"
    assert parsed.published_at.raw_text == "May 14, 2031"


def test_deadline_time_is_not_published_without_a_published_section() -> None:
    document = """<html><body>
    <h2>Application deadline</h2>
    <time datetime="2031-06-01">June 1, 2031</time>
    </body></html>"""
    request = _request("https://synthetic-meridian.example.test/careers/role")
    document_size = len(document.encode("utf-8"))
    candidate = StaticResponseCandidate(
        final_target=_prepare_initial_target(request, _resolver(_PUBLIC_IP)),
        representation=TransportRepresentation.HTML,
        document=UntrustedDocument(document),
        http_status=200,
        raw_size=document_size,
        decompressed_size=document_size,
    )

    parsed = extract_static_candidate(candidate)

    assert parsed.published_at.status.value == "unknown"
    assert parsed.published_at.value is None


def test_json_pointer_locator_validates_rfc6901_escapes_and_rejects_invalid_paths() -> None:
    document = '{"a/b":{"m~n":["first"]}}'
    valid = EvidenceDraft(
        evidence_key="valid",
        section_title=None,
        text_excerpt="first",
        locator=Locator(
            kind=LocatorKind.JSON_POINTER,
            value="/a~1b/m~0n/0",
            normalization_version=None,
            start=None,
            end=None,
        ),
        chunk_order=0,
        origin_kind="json_value",
    )

    assert validate_evidence_locator(document, valid)
    for value in ("/a~2b/m~0n/0", "/a~1b/m~0n/00", "/a~1b/m~0n/1"):
        invalid = EvidenceDraft(
            evidence_key=value,
            section_title=None,
            text_excerpt="first",
            locator=Locator(
                kind=LocatorKind.JSON_POINTER,
                value=value,
                normalization_version=None,
                start=None,
                end=None,
            ),
            chunk_order=0,
            origin_kind="json_value",
        )

        assert not validate_evidence_locator(document, invalid)


def test_http_failures_preserve_status_and_429_never_retries() -> None:
    fixture = _fixture("official_api.json")
    expected_codes = {
        "official_api_403_access_denied": StaticFetchFailureCode.ACCESS_DENIED,
        "official_api_404_not_found": StaticFetchFailureCode.NOT_FOUND,
        "official_api_429_rate_limited": StaticFetchFailureCode.RATE_LIMITED,
    }

    for case_id, failure_code in expected_codes.items():
        case = _case(fixture, case_id)
        decision = _response_decision(case)

        assert decision.candidate is None
        assert decision.failure_code is failure_code
        if failure_code is StaticFetchFailureCode.RATE_LIMITED:
            assert decision.retry_after == case["transport"]["headers"]["retry-after"]

    settings = collector_module._collector_settings(_limits())
    assert {403, 404, 429}.isdisjoint(settings["RETRY_HTTP_CODES"])


@pytest.mark.parametrize(
    ("source_url", "addresses", "expected_resolver_calls"),
    [
        ("https://127.0.0.1/private", (), []),
        ("https://loopback.synthetic.test/private", ("127.0.0.1",), ["loopback.synthetic.test"]),
    ],
)
def test_private_destinations_are_blocked_before_a_response_request(
    source_url: str,
    addresses: tuple[str, ...],
    expected_resolver_calls: list[str],
) -> None:
    fixture = _fixture("official_api.json")
    private_case = _case(fixture, "official_api_private_endpoint_excluded")
    resolver_calls: list[str] = []

    def resolver(hostname: str) -> tuple[str, ...]:
        resolver_calls.append(hostname)
        return addresses

    assert private_case["transport"]["request_performed"] is False
    with pytest.raises(UnsafeDestination):
        _prepare_initial_target(_request(source_url), resolver)

    assert resolver_calls == expected_resolver_calls
