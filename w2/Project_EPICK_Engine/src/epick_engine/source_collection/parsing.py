"""Pure parsing boundary for one collected static response."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime
from hashlib import sha256
from typing import Final, Literal, Protocol, cast

from scrapy.selector import Selector

from epick_engine.source_collection.collector import StaticResponseCandidate
from epick_engine.source_collection.contracts import (
    DatePrecision,
    DateStatus,
    DateValue,
    ExtractionStatus,
    Locator,
    LocatorKind,
    PostingSectionKind,
)
from epick_engine.source_collection.policy import Representation

HASH_PROFILE_VERSION: Final = "epick-response-sha256-v1"
PARSER_VERSION: Final = "epick-static-evidence-v3"

_ORIGIN_KIND: Final = "static_html"
_JSON_ORIGIN_KIND: Final = "json_value"
_JSON_SCRIPT_TYPES: Final = frozenset({"application/json", "application/ld+json"})
_JSON_POINTER_MISSING: Final = object()
_HEADING_TAGS: Final = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_IGNORED_TAGS: Final = frozenset({"script", "style", "template", "noscript"})
_TEXT_BOUNDARY_TAGS: Final = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_STATICALLY_VISIBLE_XPATH: Final = (
    "not(ancestor-or-self::*[@hidden]) and "
    "not(ancestor-or-self::*["
    "translate(normalize-space(@aria-hidden), 'TRUE', 'true') = 'true']) and "
    "not(ancestor-or-self::*[contains("
    "translate(@style, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ ', 'abcdefghijklmnopqrstuvwxyz'), "
    "'display:none')]) and "
    "not(ancestor-or-self::*[contains("
    "translate(@style, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ ', 'abcdefghijklmnopqrstuvwxyz'), "
    "'visibility:hidden')])"
)
_BLOCK_XPATH: Final = (
    "//*[self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or "
    "self::h6 or self::p or self::li or self::dt or self::dd or self::time or "
    "(self::strong and not(ancestor::*[self::p or self::li or self::dt or "
    "self::dd or self::td or self::th or self::h1 or self::h2 or self::h3 or "
    "self::h4 or self::h5 or self::h6 or self::span]) and "
    "not(parent::*/text()[normalize-space()]))]"
    "[not(ancestor::script) and not(ancestor::style) and "
    "not(ancestor::template) and not(ancestor::noscript)]"
    f"[{_STATICALLY_VISIBLE_XPATH}]"
)
_EXPLICIT_UNKNOWN_DATE = re.compile(
    r"\bunknown\b|\bnot (?:available|provided|specified)\b|미상|알 수 없|확인되지",
    re.IGNORECASE,
)
_SEMANTIC_UNKNOWN = re.compile(
    r"\b(?:unknown|unconfirmed|not confirmed|not (?:available|provided|specified))\b|"
    r"미상|알 수 없|확인되지",
    re.IGNORECASE,
)
_DEADLINE_TEXT = re.compile(
    r"\b(?:application(?:s)?\s+(?:close|closing)|deadline|apply\s+by)\b",
    re.IGNORECASE,
)
_PUBLISHED_TEXT = re.compile(
    r"\b(?:published|posting date|date posted)\b",
    re.IGNORECASE,
)
_DEADLINE_HEADING = re.compile(
    r"\s*(?:deadlines?|application deadline|closing dates?)\s*:?$",
    re.IGNORECASE,
)
_PUBLISHED_HEADING = re.compile(
    r"\s*(?:published|publication date|posting date|date posted)\s*:?$",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(?P<value>\d{4}-\d{2}-\d{2})\b")
_EXPLICIT_DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXPLICIT_MONTH_VALUE = re.compile(r"^\d{4}-\d{2}$")
_EXPLICIT_DATETIME_VALUE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?$"
)
_NON_REQUIRED_TEXT = re.compile(
    r"\b(?:not\s+(?:a\s+)?(?:mandatory|required)|optional)\b|"
    r"\bonly\s+as\s+(?:a\s+)?(?:general\s+)?example\b",
    re.IGNORECASE,
)
_PREFERRED_TEXT = re.compile(
    r"\b(?:preferred|nice to have|bonus|desirable)\b",
    re.IGNORECASE,
)
_PREFERRED_HEADING = re.compile(
    r"\s*bonus points for\s*(?::|…|\.\.\.)?\s*$",
    re.IGNORECASE,
)
_INLINE_QUALIFICATION_LABEL = re.compile(
    r"\s*(?:what you(?:'|’)ll bring|bonus points for)\b",
    re.IGNORECASE,
)
_REQUIRED_TEXT = re.compile(
    r"\b(?:required|must|mandatory|shall|need(?:s)?\s+to)\b",
    re.IGNORECASE,
)
_REQUIRED_HEADING = re.compile(r"\b(?:requirements?|qualifications?)\b", re.IGNORECASE)
_WHAT_YOU_BRING_HEADING = re.compile(
    r"\s*what you(?:'|’)ll bring\s*:?\s*$",
    re.IGNORECASE,
)
_DUTIES_HEADING = re.compile(
    r"\b(?:duties|responsibilities|what you(?:'|’)ll do)\b",
    re.IGNORECASE,
)
_DUTIES_STANDALONE_HEADING = re.compile(
    r"\s*(?:duties|responsibilities|what you(?:'|’)ll do)\s*:?\s*$",
    re.IGNORECASE,
)
_GENERAL_STANDALONE_HEADING = re.compile(
    r"\s*what you(?:'|’)ll get\s*:?\s*$",
    re.IGNORECASE,
)
_LOCATION_HEADING = re.compile(r"\b(?:location|workplace)\b", re.IGNORECASE)
_EMPLOYMENT_TYPE_HEADING = re.compile(
    r"\b(?:employment|job|work)\s+type\b",
    re.IGNORECASE,
)
_ROLE_HEADING = re.compile(r"\s*(?:job\s+title|role|position)\s*:?$", re.IGNORECASE)
_ORGANIZATION_HEADING = re.compile(
    r"\s*(?:organization|organisation|company|employer)\s*:?$",
    re.IGNORECASE,
)
_EXPLICIT_REQUIREMENT_RELATION = re.compile(
    r"(?=.*\b(?:must|shall|required|mandatory|need(?:s)?\s+to)\b)"
    r"(?=.*\b(?:and|or)\b)|\b(?:either\b.*\bor\b|both\b.*\band\b|and/or)\b",
    re.IGNORECASE,
)
_SAME_EXPERIENCE_RELATION = re.compile(
    r"\bsame\s+experience\b.*\b(?:may|can)\s+satisf(?:y|ies)\b",
    re.IGNORECASE,
)
_ROLE_UNKNOWN_TEXT = re.compile(
    r"\b(?:job\s+)?role\b.*\b(?:unknown|unconfirmed|not confirmed|not available)\b",
    re.IGNORECASE,
)
_ORGANIZATION_UNKNOWN_TEXT = re.compile(
    r"\b(?:hiring\s+)?organi[sz]ation\b.*"
    r"\b(?:unknown|unconfirmed|not confirmed|not available)\b",
    re.IGNORECASE,
)
_JOB_TITLE_VALUE = re.compile(
    r"\b(?:job\s+title|role)\s*:\s*(?P<value>[^.;\n]+)",
    re.IGNORECASE,
)
_ORGANIZATION_VALUE = re.compile(
    r"\b(?:organization|organisation|company|employer)\s*:\s*(?P<value>[^.;\n]+)",
    re.IGNORECASE,
)
_REFERENCE_LABEL = re.compile(
    r"^(?:[a-z0-9]+(?:[ -][a-z0-9]+){0,5}\s+)?(?:url|link|website)$",
    re.IGNORECASE,
)
_HIDDEN_STYLE = re.compile(r"(?:^|;)(?:display:none|visibility:hidden)(?:!important)?(?:;|$)")
_MAX_EVIDENCE_CANDIDATES: Final = 10_000
_MAX_DOM_ELEMENTS: Final = 20_000
_MAX_DOM_DEPTH: Final = 512
_MAX_VISIBLE_TEXT_VISITS: Final = 100_000
_MIN_EXTRACTED_OUTPUT_BYTES: Final = 64 * 1024
_MAX_EXTRACTED_OUTPUT_BYTES: Final = 8 * 1024 * 1024
_OUTPUT_AMPLIFICATION_FACTOR: Final = 4
_EXTERNAL_CSS_LIMITATION: Final = "external_css_visibility_not_evaluated"


@dataclass(frozen=True, slots=True)
class EvidenceDraft:
    """Evidence that is validated against the same response before persistence."""

    evidence_key: str
    section_title: str | None
    text_excerpt: str
    locator: Locator
    chunk_order: int
    origin_kind: str


@dataclass(frozen=True, slots=True)
class ExtractedSectionDraft:
    """Generic ordered section; semantic job-posting classification is a later phase."""

    section_key: str
    heading_raw: str | None
    text_raw: str
    evidence_keys: tuple[str, ...]
    order: int


@dataclass(frozen=True, slots=True)
class StaticParseResult:
    """Pure parse output ready for the persistence orchestration boundary."""

    content_hash: str | None
    hash_profile_version: str
    parser_version: str
    representation: Representation
    extraction_status: ExtractionStatus
    evidence: tuple[EvidenceDraft, ...]
    sections: tuple[ExtractedSectionDraft, ...]
    published_at: DateValue
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PostingSectionDraft:
    """Semantic posting section that still refers to verified draft evidence keys."""

    section_key: str
    kind: PostingSectionKind
    heading_raw: str | None
    text_raw: str
    evidence_keys: tuple[str, ...]
    order: int
    relation_text: str | None


@dataclass(frozen=True, slots=True)
class PostingIdentityValue:
    """Evidence-aware identity field with its verified supporting Evidence."""

    status: Literal["known", "unknown"]
    value: str | None
    evidence_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeadlineCandidate:
    """One explicitly stated application-deadline value and its verified evidence."""

    value: str
    raw_text: str
    evidence_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StaticPostingParseResult:
    """Semantic view of locator-verified static posting evidence."""

    sections: tuple[PostingSectionDraft, ...]
    job_title: PostingIdentityValue
    organization: PostingIdentityValue
    published_at: DateValue
    deadline: DateValue
    deadline_candidates: tuple[DeadlineCandidate, ...]


@dataclass(frozen=True, slots=True)
class _SectionCandidate:
    heading_raw: str | None
    heading_evidence_key: str | None
    evidence_keys: tuple[str, ...]
    order: int


@dataclass(frozen=True, slots=True)
class _TextToken:
    value: str


@dataclass(slots=True)
class _WorkBudget:
    remaining: int

    def consume(self) -> None:
        if self.remaining <= 0:
            raise _ExtractionLimitExceeded("visible_text_work_limit_exceeded")
        self.remaining -= 1


@dataclass(slots=True)
class _OutputBudget:
    remaining_bytes: int

    def consume_text(self, value: str) -> None:
        self.consume_bytes(len(value.encode("utf-8")))

    def consume_bytes(self, size: int) -> None:
        if size > self.remaining_bytes:
            raise _ExtractionLimitExceeded("extracted_output_limit_exceeded")
        self.remaining_bytes -= size


class _ExtractionLimitExceeded(Exception):
    def __init__(self, limitation: str) -> None:
        super().__init__(limitation)
        self.limitation = limitation


class _HtmlElement(Protocol):
    tag: object
    text: str | None
    tail: str | None

    def __iter__(self) -> Iterator[_HtmlElement]: ...

    def get(self, key: str) -> str | None: ...


type LocatorValidator = Callable[[str, EvidenceDraft], bool]


def normalize_hash_document(document: str) -> str:
    """Apply only the newline normalization frozen by the v1 hash profile."""

    return document.replace("\r\n", "\n").replace("\r", "\n")


def compute_content_hash(representation: Representation, document: str) -> str:
    """Return the v1 identity hash for one exact response representation."""

    canonical_bytes = (
        representation.value.encode("ascii")
        + b"\0"
        + normalize_hash_document(document).encode("utf-8")
    )
    return sha256(canonical_bytes).hexdigest()


def extract_static_candidate(
    candidate: StaticResponseCandidate,
    locator_validator: LocatorValidator | None = None,
) -> StaticParseResult:
    """Extract ordered, locator-verified evidence from one static response."""

    try:
        content_hash = compute_content_hash(
            candidate.representation,
            candidate.document.text,
        )
    except UnicodeEncodeError:
        return _failed_result(
            candidate.representation,
            None,
            "invalid_unicode_document",
        )
    if candidate.representation is Representation.JSON:
        return _extract_json_candidate(candidate, content_hash, locator_validator)
    return _extract_html_candidate(candidate, content_hash, locator_validator)


def _extract_html_candidate(
    candidate: StaticResponseCandidate,
    content_hash: str,
    locator_validator: LocatorValidator | None,
) -> StaticParseResult:
    try:
        document_selector = Selector(text=candidate.document.text, type="html")
        source_order_by_element = _document_positions(cast(_HtmlElement, document_selector.root))
        output_budget = _output_budget(candidate)
        block_nodes = list(document_selector.xpath(_BLOCK_XPATH))
        if len(block_nodes) > _MAX_EVIDENCE_CANDIDATES:
            return _failed_result(
                candidate.representation,
                content_hash,
                "evidence_candidate_limit_exceeded",
            )
        evidence_candidates, section_candidates, published_key = _build_candidates(
            block_nodes,
            source_order_by_element,
            output_budget,
        )
        (
            embedded_evidence,
            embedded_sections,
            embedded_partial,
            embedded_limitations,
        ) = _embedded_json_candidates(
            document_selector,
            evidence_candidates,
            output_budget,
            section_order_start=len(section_candidates),
        )
        evidence_candidates += embedded_evidence
        section_candidates += embedded_sections
        if len(evidence_candidates) > _MAX_EVIDENCE_CANDIDATES:
            return _failed_result(
                candidate.representation,
                content_hash,
                "evidence_candidate_limit_exceeded",
            )
    except RecursionError:
        return _failed_result(
            candidate.representation,
            content_hash,
            "dom_depth_limit_exceeded",
        )
    except _ExtractionLimitExceeded as error:
        return _failed_result(
            candidate.representation,
            content_hash,
            error.limitation,
        )
    except (TypeError, ValueError):
        return _failed_result(
            candidate.representation,
            content_hash,
            "invalid_html_document",
        )
    if not evidence_candidates:
        if embedded_limitations:
            return _failed_result(
                candidate.representation,
                content_hash,
                embedded_limitations[0],
            )
        return _failed_result(
            candidate.representation,
            content_hash,
            "no_extractable_evidence",
        )

    valid_evidence = _validated_evidence(
        candidate.document.text,
        evidence_candidates,
        locator_validator,
    )
    rejected_count = len(evidence_candidates) - len(valid_evidence)
    if not valid_evidence:
        return _failed_result(
            candidate.representation,
            content_hash,
            "no_verifiable_evidence",
        )

    valid_keys = {evidence.evidence_key for evidence in valid_evidence}
    valid_evidence = _sanitize_section_titles(
        valid_evidence,
        section_candidates,
        valid_keys,
    )
    evidence_by_key = {evidence.evidence_key: evidence for evidence in valid_evidence}
    try:
        sections = _finalize_sections(section_candidates, evidence_by_key, output_budget)
    except _ExtractionLimitExceeded as error:
        return _failed_result(
            candidate.representation,
            content_hash,
            error.limitation,
        )
    published_at = _html_time_published_at(document_selector, valid_evidence, sections)
    if published_at is None:
        json_published_at = _json_published_at(
            tuple(
                evidence
                for evidence in valid_evidence
                if evidence.locator.kind is LocatorKind.JSON_POINTER
            )
        )
        if json_published_at.status is DateStatus.KNOWN:
            published_at = json_published_at
        else:
            published_raw_text = (
                evidence_by_key[published_key].text_excerpt
                if published_key is not None and published_key in evidence_by_key
                else None
            )
            published_at = _unknown_published_at(
                published_raw_text
                if published_raw_text is not None
                and _EXPLICIT_UNKNOWN_DATE.search(published_raw_text)
                else None
            )
    limitations = (
        (_EXTERNAL_CSS_LIMITATION,)
        + embedded_limitations
        + ((f"unverifiable_locator_count:{rejected_count}",) if rejected_count else ())
    )
    status = (
        ExtractionStatus.PARTIAL
        if embedded_partial or rejected_count
        else ExtractionStatus.COMPLETE
    )
    return StaticParseResult(
        content_hash=content_hash,
        hash_profile_version=HASH_PROFILE_VERSION,
        parser_version=PARSER_VERSION,
        representation=candidate.representation,
        extraction_status=status,
        evidence=valid_evidence,
        sections=sections,
        published_at=published_at,
        limitations=limitations,
    )


def _extract_json_candidate(
    candidate: StaticResponseCandidate,
    content_hash: str,
    locator_validator: LocatorValidator | None,
) -> StaticParseResult:
    try:
        payload = _parse_json_document(candidate.document.text)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return _failed_result(candidate.representation, content_hash, "malformed_json")

    try:
        output_budget = _output_budget(candidate)
        evidence_candidates, partial_response = _json_evidence_candidates(
            payload,
            source_scope="direct_json",
            output_budget=output_budget,
            chunk_order_start=0,
        )
    except _ExtractionLimitExceeded as error:
        return _failed_result(candidate.representation, content_hash, error.limitation)
    if not evidence_candidates:
        if partial_response:
            return StaticParseResult(
                content_hash=content_hash,
                hash_profile_version=HASH_PROFILE_VERSION,
                parser_version=PARSER_VERSION,
                representation=candidate.representation,
                extraction_status=ExtractionStatus.PARTIAL,
                evidence=(),
                sections=(),
                published_at=_unknown_published_at(None),
                limitations=("partial_json_response",),
            )
        return _failed_result(candidate.representation, content_hash, "no_extractable_evidence")

    valid_evidence = _validated_evidence(
        candidate.document.text,
        evidence_candidates,
        locator_validator,
    )
    rejected_count = len(evidence_candidates) - len(valid_evidence)
    if not valid_evidence:
        return _failed_result(candidate.representation, content_hash, "no_verifiable_evidence")

    section_candidates = _json_section_candidates(
        payload,
        valid_evidence,
        start_order=0,
        partial_response=partial_response,
    )
    valid_keys = {evidence.evidence_key for evidence in valid_evidence}
    valid_evidence = _sanitize_section_titles(
        valid_evidence,
        section_candidates,
        valid_keys,
    )
    evidence_by_key = {evidence.evidence_key: evidence for evidence in valid_evidence}
    try:
        sections = _finalize_sections(
            section_candidates,
            evidence_by_key,
            output_budget,
        )
    except _ExtractionLimitExceeded as error:
        return _failed_result(candidate.representation, content_hash, error.limitation)

    limitations: tuple[str, ...] = ()
    if partial_response:
        limitations += ("partial_json_response",)
    if rejected_count:
        limitations += (f"unverifiable_locator_count:{rejected_count}",)
    return StaticParseResult(
        content_hash=content_hash,
        hash_profile_version=HASH_PROFILE_VERSION,
        parser_version=PARSER_VERSION,
        representation=candidate.representation,
        extraction_status=(
            ExtractionStatus.PARTIAL
            if partial_response or rejected_count
            else ExtractionStatus.COMPLETE
        ),
        evidence=valid_evidence,
        sections=sections,
        published_at=_json_published_at(valid_evidence),
        limitations=limitations,
    )


def _output_budget(candidate: StaticResponseCandidate) -> _OutputBudget:
    return _OutputBudget(
        min(
            _MAX_EXTRACTED_OUTPUT_BYTES,
            max(
                _MIN_EXTRACTED_OUTPUT_BYTES,
                candidate.decompressed_size * _OUTPUT_AMPLIFICATION_FACTOR,
            ),
        )
    )


def _validated_evidence(
    document: str,
    evidence_candidates: tuple[EvidenceDraft, ...],
    locator_validator: LocatorValidator | None,
) -> tuple[EvidenceDraft, ...]:
    if locator_validator is not None:
        return tuple(
            evidence for evidence in evidence_candidates if locator_validator(document, evidence)
        )
    locator_index = {
        evidence.locator.value: evidence.text_excerpt
        for evidence in evidence_candidates
        if evidence.locator.kind is LocatorKind.XPATH
    }
    return tuple(
        evidence
        for evidence in evidence_candidates
        if evidence.locator.kind is LocatorKind.JSON_POINTER
        or _validate_indexed_locator(locator_index, evidence)
    )


def _parse_json_document(document: str) -> object:
    def preserve_unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def reject_non_standard_constant(_value: str) -> object:
        raise ValueError("non-standard JSON constant")

    return cast(
        object,
        json.loads(
            document,
            object_pairs_hook=preserve_unique_object,
            parse_constant=reject_non_standard_constant,
        ),
    )


def _json_evidence_candidates(
    payload: object,
    *,
    source_scope: str,
    output_budget: _OutputBudget,
    chunk_order_start: int,
) -> tuple[tuple[EvidenceDraft, ...], bool]:
    partial_response = _json_has_partial_signal(payload)
    evidence: list[EvidenceDraft] = []
    work_budget = _WorkBudget(_MAX_VISIBLE_TEXT_VISITS)
    stack: list[tuple[object, str, int]] = [(payload, "", 0)]

    while stack:
        value, pointer, depth = stack.pop()
        work_budget.consume()
        if depth > _MAX_DOM_DEPTH:
            raise _ExtractionLimitExceeded("json_depth_limit_exceeded")
        if isinstance(value, str):
            if not pointer:
                continue
            if len(evidence) >= _MAX_EVIDENCE_CANDIDATES:
                raise _ExtractionLimitExceeded("evidence_candidate_limit_exceeded")
            locator = Locator(
                kind=LocatorKind.JSON_POINTER,
                value=pointer,
                normalization_version=None,
                start=None,
                end=None,
            )
            output_budget.consume_text(value)
            output_budget.consume_text(locator.value)
            evidence.append(
                EvidenceDraft(
                    evidence_key=_stable_key(
                        "evidence",
                        locator.kind.value,
                        locator.value,
                        value,
                        _JSON_ORIGIN_KIND,
                        source_scope,
                    ),
                    section_title=None,
                    text_excerpt=value,
                    locator=locator,
                    chunk_order=chunk_order_start + len(evidence),
                    origin_kind=_JSON_ORIGIN_KIND,
                )
            )
            continue
        if isinstance(value, dict):
            items = tuple(cast(dict[str, object], value).items())
            for key, child in reversed(items):
                if not pointer and key in {"next_cursor", "warnings"}:
                    continue
                stack.append((child, _json_pointer_child(pointer, key), depth + 1))
            continue
        if isinstance(value, list):
            array_items = cast(list[object], value)
            for index in range(len(array_items) - 1, -1, -1):
                stack.append(
                    (array_items[index], _json_pointer_child(pointer, str(index)), depth + 1)
                )

    return tuple(evidence), partial_response


def _json_has_partial_signal(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    root = cast(dict[str, object], payload)
    if root.get("next_cursor") is not None:
        return True
    warnings = root.get("warnings")
    if warnings is None:
        return False
    if isinstance(warnings, (str, list, dict)):
        return bool(warnings)
    return True


def _json_pointer_child(parent: str, key: str) -> str:
    escaped = key.replace("~", "~0").replace("/", "~1")
    return parent + "/" + escaped


def _json_section_candidates(
    payload: object,
    evidence: tuple[EvidenceDraft, ...],
    *,
    start_order: int,
    partial_response: bool,
) -> tuple[_SectionCandidate, ...]:
    if not evidence:
        return ()
    if partial_response:
        return (
            _json_section_candidate(
                evidence,
                order=start_order,
                heading_raw=_partial_json_heading(payload),
                heading_evidence_key=None,
            ),
        )
    if not isinstance(payload, dict):
        return (
            _json_section_candidate(
                evidence,
                order=start_order,
                heading_raw="JSON",
                heading_evidence_key=None,
            ),
        )

    sections: list[_SectionCandidate] = []
    for key, value in cast(dict[str, object], payload).items():
        if key in {"next_cursor", "warnings"} or not isinstance(value, (dict, list)):
            continue
        pointer = _json_pointer_child("", key)
        section_evidence = tuple(
            item
            for item in evidence
            if item.locator.value == pointer or item.locator.value.startswith(pointer + "/")
        )
        if not section_evidence:
            continue
        heading_raw, heading_evidence_key = _json_container_heading(
            key,
            value,
            section_evidence,
        )
        sections.append(
            _json_section_candidate(
                section_evidence,
                order=start_order + len(sections),
                heading_raw=heading_raw,
                heading_evidence_key=heading_evidence_key,
            )
        )
    return tuple(sections)


def _json_section_candidate(
    evidence: tuple[EvidenceDraft, ...],
    *,
    order: int,
    heading_raw: str | None,
    heading_evidence_key: str | None,
) -> _SectionCandidate:
    return _SectionCandidate(
        heading_raw=heading_raw,
        heading_evidence_key=heading_evidence_key,
        evidence_keys=tuple(item.evidence_key for item in evidence),
        order=order,
    )


def _json_container_heading(
    key: str,
    value: object,
    evidence: tuple[EvidenceDraft, ...],
) -> tuple[str, str | None]:
    if isinstance(value, dict):
        name_pointer = _json_pointer_child(_json_pointer_child("", key), "name")
        name_evidence = next(
            (item for item in evidence if item.locator.value == name_pointer),
            None,
        )
        if name_evidence is not None:
            return name_evidence.text_excerpt, name_evidence.evidence_key
    return _json_section_heading(key), None


def _partial_json_heading(payload: object) -> str:
    if isinstance(payload, dict):
        for key, value in cast(dict[str, object], payload).items():
            if key not in {"next_cursor", "warnings"} and isinstance(value, list):
                return "Partial " + _json_section_label(key)
    return "Partial response"


def _json_section_heading(key: str) -> str:
    label = _json_section_label(key)
    return label[:1].upper() + label[1:]


def _json_section_label(key: str) -> str:
    return key.replace("_", " ").replace("-", " ")


def _embedded_json_candidates(
    document_selector: Selector,
    html_evidence: tuple[EvidenceDraft, ...],
    output_budget: _OutputBudget,
    *,
    section_order_start: int,
) -> tuple[
    tuple[EvidenceDraft, ...],
    tuple[_SectionCandidate, ...],
    bool,
    tuple[str, ...],
]:
    evidence_groups: list[tuple[tuple[EvidenceDraft, ...], str | None, str | None]] = []
    limitations: list[str] = []
    partial = False
    next_chunk_order = 0

    for script_index, script in enumerate(_embedded_json_scripts(document_selector)):
        raw_payload = script.xpath("string()").get()
        if not isinstance(raw_payload, str):
            partial = True
            _append_limitation(limitations, "malformed_embedded_json")
            continue
        try:
            payload = _parse_json_document(raw_payload)
        except (json.JSONDecodeError, RecursionError, ValueError):
            partial = True
            _append_limitation(limitations, "malformed_embedded_json")
            continue
        candidates, partial_response = _json_evidence_candidates(
            payload,
            source_scope=f"embedded_json:{script_index}",
            output_budget=output_budget,
            chunk_order_start=next_chunk_order,
        )
        if not candidates:
            partial = True
            _append_limitation(limitations, "no_embedded_json_string_evidence")
            continue
        heading_raw, heading_evidence_key = _embedded_json_heading(script, html_evidence)
        evidence_groups.append((candidates, heading_raw, heading_evidence_key))
        next_chunk_order += len(candidates)
        if partial_response:
            partial = True
            _append_limitation(limitations, "partial_json_response")

    occurrence_count: dict[tuple[str, str], int] = {}
    for group, _, _ in evidence_groups:
        for item in group:
            identity = (item.locator.value, item.text_excerpt)
            occurrence_count[identity] = occurrence_count.get(identity, 0) + 1
    ambiguous = {identity for identity, count in occurrence_count.items() if count > 1}
    if ambiguous:
        partial = True
        _append_limitation(limitations, "ambiguous_embedded_json_pointer")

    evidence_items: list[EvidenceDraft] = []
    sections: list[_SectionCandidate] = []
    for group, heading_raw, heading_evidence_key in evidence_groups:
        unambiguous_group = tuple(
            item for item in group if (item.locator.value, item.text_excerpt) not in ambiguous
        )
        if not unambiguous_group:
            continue
        evidence_items.extend(unambiguous_group)
        sections.append(
            _json_section_candidate(
                unambiguous_group,
                order=section_order_start + len(sections),
                heading_raw=heading_raw,
                heading_evidence_key=heading_evidence_key,
            )
        )
    return tuple(evidence_items), tuple(sections), partial, tuple(limitations)


def _embedded_json_heading(
    script: Selector,
    html_evidence: tuple[EvidenceDraft, ...],
) -> tuple[str | None, str | None]:
    evidence_by_locator = {
        item.locator.value: item for item in html_evidence if item.locator.kind is LocatorKind.XPATH
    }
    heading_nodes = script.xpath(
        "(preceding::*[self::h1 or self::h2 or self::h3 or "
        "self::h4 or self::h5 or self::h6])[last()]"
    )
    if not heading_nodes:
        return None, None
    heading = evidence_by_locator.get(_absolute_xpath(heading_nodes[0]))
    if heading is None:
        return None, None
    return heading.text_excerpt, heading.evidence_key


def _embedded_json_scripts(document_selector: Selector) -> tuple[Selector, ...]:
    scripts: list[Selector] = []
    for script in document_selector.xpath("//script[@type]"):
        content_type = script.xpath("string(@type)").get()
        if not isinstance(content_type, str):
            continue
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type in _JSON_SCRIPT_TYPES:
            scripts.append(script)
    return tuple(scripts)


def _append_limitation(limitations: list[str], value: str) -> None:
    if value not in limitations:
        limitations.append(value)


def _json_published_at(evidence: tuple[EvidenceDraft, ...]) -> DateValue:
    top_level = next(
        (item for item in evidence if item.locator.value == "/published_at"),
        None,
    )
    if top_level is not None:
        return _published_at_from_explicit_value(top_level.text_excerpt)
    for item in evidence:
        if item.locator.value.rsplit("/", 1)[-1] == "published_at":
            return _published_at_from_explicit_value(item.text_excerpt)
    return _unknown_published_at(None)


def _published_at_from_explicit_value(value: str) -> DateValue:
    parsed = _explicit_date_value(value, raw_text=value, timezone=None)
    return parsed if parsed is not None else _unknown_published_at(value or None)


def _html_time_published_at(
    document_selector: Selector,
    evidence: tuple[EvidenceDraft, ...],
    source_sections: tuple[ExtractedSectionDraft, ...],
) -> DateValue | None:
    evidence_by_locator = {
        item.locator.value: item for item in evidence if item.locator.kind is LocatorKind.XPATH
    }
    evidence_by_key = {item.evidence_key: item for item in evidence}
    semantic_sections = _semantic_sections(
        source_sections,
        evidence_by_key,
        _classify_posting_section,
    )
    published_evidence_keys = {
        evidence_key
        for section in semantic_sections
        if section.kind is PostingSectionKind.PUBLISHED
        for evidence_key in section.evidence_keys
    }
    deadline_evidence_keys = {
        evidence_key
        for section in semantic_sections
        if section.kind is PostingSectionKind.DEADLINE
        for evidence_key in section.evidence_keys
    }
    time_candidates: list[tuple[EvidenceDraft, DateValue | None]] = []
    for time_node in document_selector.xpath("//time"):
        evidence_item = evidence_by_locator.get(_absolute_xpath(time_node))
        if evidence_item is None:
            continue
        datetime_value = time_node.xpath("string(@datetime)").get()
        if not isinstance(datetime_value, str):
            continue
        raw_text = evidence_item.text_excerpt
        parsed = _explicit_date_value(
            datetime_value.strip(),
            raw_text=raw_text,
            timezone=_html_time_timezone(datetime_value.strip(), raw_text),
        )
        time_candidates.append((evidence_item, parsed))
    if not time_candidates:
        return None

    published_candidates = tuple(
        candidate
        for candidate in time_candidates
        if candidate[0].evidence_key in published_evidence_keys
    )
    selected_candidates = published_candidates or tuple(
        candidate
        for candidate in time_candidates
        if candidate[0].evidence_key not in deadline_evidence_keys
    )
    if not selected_candidates:
        return _unknown_published_at(None)
    invalid_raw_text = next(
        (
            evidence_item.text_excerpt or None
            for evidence_item, parsed in selected_candidates
            if parsed is None
        ),
        None,
    )
    if invalid_raw_text is not None:
        return _unknown_published_at(invalid_raw_text)
    parsed_times = tuple(parsed for _, parsed in selected_candidates if parsed is not None)
    if not parsed_times:
        return None
    first = parsed_times[0]
    if any(
        (item.value, item.precision, item.timezone)
        != (first.value, first.precision, first.timezone)
        for item in parsed_times[1:]
    ):
        return _conflicting_date()
    return first


def _html_time_timezone(value: str, raw_text: str) -> str | None:
    if value.endswith("+09:00") and raw_text.endswith(" KST"):
        return "Asia/Seoul"
    return None


def _explicit_date_value(
    value: str,
    *,
    raw_text: str,
    timezone: str | None,
) -> DateValue | None:
    if not raw_text:
        return None
    try:
        if _EXPLICIT_DATE_VALUE.fullmatch(value):
            date.fromisoformat(value)
            precision = DatePrecision.DATE
        elif _EXPLICIT_MONTH_VALUE.fullmatch(value):
            date.fromisoformat(value + "-01")
            precision = DatePrecision.MONTH
        elif _EXPLICIT_DATETIME_VALUE.fullmatch(value):
            datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
            precision = DatePrecision.DATETIME
        else:
            return None
    except ValueError:
        return None
    return DateValue(
        status=DateStatus.KNOWN,
        raw_text=raw_text,
        value=value,
        precision=precision,
        timezone=timezone,
    )


def parse_static_posting(parsed: StaticParseResult) -> StaticPostingParseResult:
    """Classify verified static evidence without reparsing HTML or inferring identities."""

    return _parse_static_posting_with_classifier(parsed, _classify_posting_section)


def _parse_static_posting_with_classifier(
    parsed: StaticParseResult,
    classify_section: Callable[[str, str | None], PostingSectionKind],
    standalone_heading_kind: Callable[[str], PostingSectionKind | None] | None = None,
) -> StaticPostingParseResult:
    evidence_by_key = {evidence.evidence_key: evidence for evidence in parsed.evidence}
    sections = _semantic_sections(
        parsed.sections,
        evidence_by_key,
        classify_section,
        standalone_heading_kind,
    )
    job_title, organization = _identity_values(sections, evidence_by_key)
    published_at = _semantic_published_at(
        parsed.published_at,
        sections,
        evidence_by_key,
    )
    deadline_candidates = _deadline_candidates(sections, evidence_by_key)
    has_invalid_deadline_date = _has_invalid_deadline_date(sections, evidence_by_key)
    return StaticPostingParseResult(
        sections=sections,
        job_title=job_title,
        organization=organization,
        published_at=published_at,
        deadline=_deadline_value(deadline_candidates, has_invalid_deadline_date),
        deadline_candidates=deadline_candidates,
    )


def parse_approved_static_posting(parsed: StaticParseResult) -> StaticPostingParseResult:
    """Allow one verified H1 title for a policy-authorized individual job posting."""

    posting = _parse_static_posting_with_classifier(
        parsed,
        _classify_approved_posting_section,
        _approved_standalone_heading_kind,
    )
    evidence_by_key = {evidence.evidence_key: evidence for evidence in parsed.evidence}
    h1_evidence_keys = _h1_evidence_keys(posting.sections, evidence_by_key)
    if any(
        _SEMANTIC_UNKNOWN.search(evidence_by_key[evidence_key].text_excerpt)
        for evidence_key in h1_evidence_keys
    ):
        return replace(
            posting,
            job_title=PostingIdentityValue(
                status="unknown",
                value=None,
                evidence_keys=h1_evidence_keys,
            ),
        )
    if (
        posting.job_title.status == "known"
        or posting.job_title.evidence_keys
        or parsed.extraction_status is not ExtractionStatus.COMPLETE
    ):
        return posting
    return replace(
        posting,
        job_title=_identity_value_from_verified_h1(posting.sections, evidence_by_key),
    )


def _semantic_sections(
    source_sections: tuple[ExtractedSectionDraft, ...],
    evidence_by_key: dict[str, EvidenceDraft],
    classify_section: Callable[[str, str | None], PostingSectionKind],
    standalone_heading_kind: Callable[[str], PostingSectionKind | None] | None = None,
) -> tuple[PostingSectionDraft, ...]:
    sections: list[PostingSectionDraft] = []
    section_key_counts: dict[str, int] = {}

    for source_section in source_sections:
        evidence_keys = tuple(
            evidence_key
            for evidence_key in source_section.evidence_keys
            if evidence_key in evidence_by_key
        )
        heading_key = _heading_evidence_key(source_section, evidence_by_key)
        content_keys = tuple(key for key in evidence_keys if key != heading_key)
        active_heading_raw = source_section.heading_raw
        active_heading_kind: PostingSectionKind | None = None
        heading_key_pending = (
            heading_key
            if _should_bind_heading_evidence(
                source_section.heading_raw,
                content_keys,
                evidence_by_key,
            )
            else None
        )

        if heading_key is not None and _is_h1_evidence(evidence_by_key[heading_key]):
            title_text = evidence_by_key[heading_key].text_excerpt
            section_key = _unique_section_key(
                _semantic_section_key(
                    PostingSectionKind.TITLE,
                    title_text,
                    (heading_key,),
                    source_section.section_key,
                    len(sections),
                ),
                section_key_counts,
            )
            sections.append(
                PostingSectionDraft(
                    section_key=section_key,
                    kind=PostingSectionKind.TITLE,
                    heading_raw=source_section.heading_raw,
                    text_raw=title_text,
                    evidence_keys=(heading_key,),
                    order=len(sections),
                    relation_text=None,
                )
            )
            heading_key_pending = None

        for evidence_key in content_keys:
            evidence = evidence_by_key[evidence_key]
            text_raw = evidence.text_excerpt
            if _is_reference_label(text_raw):
                continue
            virtual_heading_kind = (
                standalone_heading_kind(text_raw) if standalone_heading_kind is not None else None
            )
            if virtual_heading_kind is not None:
                active_heading_raw = text_raw
                active_heading_kind = virtual_heading_kind
                heading_key_pending = evidence_key
                continue
            kind = active_heading_kind or classify_section(text_raw, active_heading_raw)
            section_evidence_keys = (
                (heading_key_pending, evidence_key)
                if heading_key_pending is not None
                else (evidence_key,)
            )
            section_key = _unique_section_key(
                _semantic_section_key(
                    kind,
                    text_raw,
                    section_evidence_keys,
                    source_section.section_key,
                    len(sections),
                ),
                section_key_counts,
            )
            sections.append(
                PostingSectionDraft(
                    section_key=section_key,
                    kind=kind,
                    heading_raw=active_heading_raw,
                    text_raw=text_raw,
                    evidence_keys=section_evidence_keys,
                    order=len(sections),
                    relation_text=_relation_text(text_raw),
                )
            )
            heading_key_pending = None

    return tuple(sections)


def _heading_evidence_key(
    source_section: ExtractedSectionDraft,
    evidence_by_key: dict[str, EvidenceDraft],
) -> str | None:
    if source_section.heading_raw is None:
        return None
    return next(
        (
            evidence_key
            for evidence_key in source_section.evidence_keys
            if (evidence := evidence_by_key.get(evidence_key)) is not None
            and evidence.text_excerpt == source_section.heading_raw
        ),
        None,
    )


def _is_h1_evidence(evidence: EvidenceDraft) -> bool:
    return (
        evidence.locator.kind is LocatorKind.XPATH
        and re.search(r"/h1(?:\[|$)", evidence.locator.value, re.IGNORECASE) is not None
    )


def _should_bind_heading_evidence(
    heading_raw: str | None,
    content_keys: tuple[str, ...],
    evidence_by_key: dict[str, EvidenceDraft],
) -> bool:
    if heading_raw is None:
        return False
    substantive_keys = tuple(
        evidence_key
        for evidence_key in content_keys
        if not _is_reference_label(evidence_by_key[evidence_key].text_excerpt)
    )
    return len(substantive_keys) > 1 or _REQUIRED_HEADING.search(heading_raw) is None


def _is_reference_label(text: str) -> bool:
    return _REFERENCE_LABEL.fullmatch(text.strip()) is not None


def _classify_posting_section(
    text_raw: str,
    heading_raw: str | None,
) -> PostingSectionKind:
    heading = heading_raw or ""
    if _DUTIES_HEADING.search(heading):
        return PostingSectionKind.DUTIES
    if _DEADLINE_TEXT.search(text_raw) or _DEADLINE_HEADING.fullmatch(heading):
        return PostingSectionKind.DEADLINE
    if _PUBLISHED_TEXT.search(text_raw) or _PUBLISHED_HEADING.fullmatch(heading):
        return PostingSectionKind.PUBLISHED
    if _NON_REQUIRED_TEXT.search(text_raw):
        return PostingSectionKind.GENERAL
    if _SAME_EXPERIENCE_RELATION.search(text_raw):
        return PostingSectionKind.GENERAL
    if _PREFERRED_TEXT.search(text_raw):
        return PostingSectionKind.PREFERRED
    if _LOCATION_HEADING.search(heading) or re.match(r"\s*location\s*:", text_raw, re.I):
        return PostingSectionKind.LOCATION
    if _EMPLOYMENT_TYPE_HEADING.search(heading) or re.match(
        r"\s*(?:employment|job|work)\s+type\s*:",
        text_raw,
        re.IGNORECASE,
    ):
        return PostingSectionKind.EMPLOYMENT_TYPE
    if _REQUIRED_TEXT.search(text_raw) or _REQUIRED_HEADING.search(heading):
        return PostingSectionKind.REQUIRED
    if _ROLE_HEADING.fullmatch(heading) is not None:
        return PostingSectionKind.ROLE
    if _ORGANIZATION_HEADING.fullmatch(heading) is not None:
        return PostingSectionKind.ORGANIZATION
    return PostingSectionKind.GENERAL


def _approved_standalone_heading_kind(text_raw: str) -> PostingSectionKind | None:
    if _DUTIES_STANDALONE_HEADING.fullmatch(text_raw):
        return PostingSectionKind.DUTIES
    if _WHAT_YOU_BRING_HEADING.fullmatch(text_raw):
        return PostingSectionKind.REQUIRED
    if _PREFERRED_HEADING.fullmatch(text_raw):
        return PostingSectionKind.PREFERRED
    if _GENERAL_STANDALONE_HEADING.fullmatch(text_raw):
        return PostingSectionKind.GENERAL
    return None


def _classify_approved_posting_section(
    text_raw: str,
    heading_raw: str | None,
) -> PostingSectionKind:
    heading = heading_raw or ""
    if _PREFERRED_HEADING.fullmatch(heading):
        return PostingSectionKind.PREFERRED
    if _WHAT_YOU_BRING_HEADING.fullmatch(heading):
        return PostingSectionKind.REQUIRED

    kind = _classify_posting_section(text_raw, heading_raw)
    if _INLINE_QUALIFICATION_LABEL.match(text_raw) and kind is PostingSectionKind.PREFERRED:
        if _REQUIRED_HEADING.search(heading):
            return PostingSectionKind.REQUIRED
        return PostingSectionKind.GENERAL
    return kind


def _relation_text(text_raw: str) -> str | None:
    if _NON_REQUIRED_TEXT.search(text_raw):
        return None
    if _SAME_EXPERIENCE_RELATION.search(text_raw):
        return text_raw
    if _EXPLICIT_REQUIREMENT_RELATION.search(text_raw):
        return text_raw
    return None


def _semantic_section_key(
    kind: PostingSectionKind,
    text_raw: str,
    evidence_keys: tuple[str, ...],
    source_section_key: str,
    order: int,
) -> str:
    return _stable_key(
        "posting_section",
        kind.value,
        source_section_key,
        text_raw,
        "\0".join(evidence_keys),
        str(order),
    )


def _unique_section_key(section_key: str, counts: dict[str, int]) -> str:
    occurrence = counts.get(section_key, 0)
    counts[section_key] = occurrence + 1
    if occurrence == 0:
        return section_key
    return f"{section_key}_{occurrence + 1}"


def _identity_values(
    sections: tuple[PostingSectionDraft, ...],
    evidence_by_key: dict[str, EvidenceDraft],
) -> tuple[PostingIdentityValue, PostingIdentityValue]:
    title_evidence_keys = _title_evidence_keys(sections)
    role_unknown_keys: list[str] = []
    organization_unknown_keys: list[str] = []

    for section in sections:
        for evidence_key in section.evidence_keys:
            text_raw = evidence_by_key[evidence_key].text_excerpt
            if _ROLE_UNKNOWN_TEXT.search(text_raw):
                role_unknown_keys.append(evidence_key)
            if _ORGANIZATION_UNKNOWN_TEXT.search(text_raw):
                organization_unknown_keys.append(evidence_key)

    if role_unknown_keys:
        job_title = PostingIdentityValue(
            status="unknown",
            value=None,
            evidence_keys=_unique_evidence_keys((*title_evidence_keys, *role_unknown_keys)),
        )
    else:
        job_title = _identity_value_from_explicit_heading(
            sections,
            _ROLE_HEADING,
        ) or _explicit_identity_value(sections, evidence_by_key, _JOB_TITLE_VALUE)

    if organization_unknown_keys:
        organization = PostingIdentityValue(
            status="unknown",
            value=None,
            evidence_keys=_unique_evidence_keys(organization_unknown_keys),
        )
    else:
        organization = _identity_value_from_explicit_heading(
            sections,
            _ORGANIZATION_HEADING,
        ) or _explicit_identity_value(sections, evidence_by_key, _ORGANIZATION_VALUE)
    return job_title, organization


def _title_evidence_keys(sections: tuple[PostingSectionDraft, ...]) -> tuple[str, ...]:
    return _unique_evidence_keys(
        evidence_key
        for section in sections
        if section.kind is PostingSectionKind.TITLE
        for evidence_key in section.evidence_keys
    )


def _identity_value_from_verified_h1(
    sections: tuple[PostingSectionDraft, ...],
    evidence_by_key: dict[str, EvidenceDraft],
) -> PostingIdentityValue:
    h1_evidence_keys = _h1_evidence_keys(sections, evidence_by_key)
    if len(h1_evidence_keys) != 1:
        return PostingIdentityValue(
            status="unknown",
            value=None,
            evidence_keys=h1_evidence_keys,
        )
    evidence_key = h1_evidence_keys[0]
    value = evidence_by_key[evidence_key].text_excerpt.strip()
    if not value or _SEMANTIC_UNKNOWN.search(value):
        return PostingIdentityValue(
            status="unknown",
            value=None,
            evidence_keys=h1_evidence_keys,
        )
    return PostingIdentityValue(
        status="known",
        value=value,
        evidence_keys=h1_evidence_keys,
    )


def _h1_evidence_keys(
    sections: tuple[PostingSectionDraft, ...],
    evidence_by_key: dict[str, EvidenceDraft],
) -> tuple[str, ...]:
    return _unique_evidence_keys(
        evidence_key
        for section in sections
        if section.kind is PostingSectionKind.TITLE
        for evidence_key in section.evidence_keys
        if (evidence := evidence_by_key.get(evidence_key)) is not None and _is_h1_evidence(evidence)
    )


def _explicit_identity_value(
    sections: tuple[PostingSectionDraft, ...],
    evidence_by_key: dict[str, EvidenceDraft],
    pattern: re.Pattern[str],
) -> PostingIdentityValue:
    for section in sections:
        for evidence_key in section.evidence_keys:
            match = pattern.search(evidence_by_key[evidence_key].text_excerpt)
            if match is not None:
                value = match.group("value").strip()
                if value:
                    return PostingIdentityValue(
                        status="known",
                        value=value,
                        evidence_keys=(evidence_key,),
                    )
    return PostingIdentityValue(status="unknown", value=None, evidence_keys=())


def _identity_value_from_explicit_heading(
    sections: tuple[PostingSectionDraft, ...],
    heading_pattern: re.Pattern[str],
) -> PostingIdentityValue | None:
    candidates = tuple(
        (section.text_raw.strip(), section.evidence_keys)
        for section in sections
        if heading_pattern.fullmatch(section.heading_raw or "") is not None
        and section.text_raw.strip()
    )
    if not candidates:
        return None
    evidence_keys = _unique_evidence_keys(
        evidence_key for _, keys in candidates for evidence_key in keys
    )
    values = {value for value, _ in candidates}
    if len(values) != 1 or any(_SEMANTIC_UNKNOWN.search(value) for value in values):
        return PostingIdentityValue(
            status="unknown",
            value=None,
            evidence_keys=evidence_keys,
        )
    value, value_evidence_keys = candidates[0]
    return PostingIdentityValue(
        status="known",
        value=value,
        evidence_keys=value_evidence_keys,
    )


def _unique_evidence_keys(evidence_keys: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique_keys: list[str] = []
    for evidence_key in evidence_keys:
        if evidence_key not in seen:
            seen.add(evidence_key)
            unique_keys.append(evidence_key)
    return tuple(unique_keys)


def _semantic_published_at(
    fallback: DateValue,
    sections: tuple[PostingSectionDraft, ...],
    evidence_by_key: dict[str, EvidenceDraft],
) -> DateValue:
    if fallback.status is DateStatus.CONFLICTING:
        return fallback
    known_dates: list[tuple[str, str]] = []
    unknown_texts: list[str] = []
    invalid_date_texts: list[str] = []
    for section in sections:
        if section.kind is not PostingSectionKind.PUBLISHED:
            continue
        for evidence_key in section.evidence_keys:
            text_raw = evidence_by_key[evidence_key].text_excerpt
            if _EXPLICIT_UNKNOWN_DATE.search(text_raw):
                unknown_texts.append(text_raw)
            for date_match in _ISO_DATE.finditer(text_raw):
                value = date_match.group("value")
                if _is_calendar_iso_date(value):
                    known_dates.append((value, text_raw))
                else:
                    invalid_date_texts.append(text_raw)
    if known_dates:
        values = {value for value, _ in known_dates}
        fallback_disagrees = (
            fallback.status is DateStatus.KNOWN
            and fallback.value is not None
            and fallback.value not in values
        )
        explicit_unknown_fallback = (
            fallback.status is DateStatus.UNKNOWN and fallback.raw_text is not None
        )
        if (
            len(values) != 1
            or unknown_texts
            or invalid_date_texts
            or fallback_disagrees
            or explicit_unknown_fallback
        ):
            return _conflicting_date()
        value, raw_text = known_dates[0]
        return DateValue(
            status=DateStatus.KNOWN,
            raw_text=raw_text,
            value=value,
            precision=DatePrecision.DATE,
            timezone=None,
        )
    if unknown_texts:
        if fallback.status is DateStatus.KNOWN:
            return _conflicting_date()
        return _unknown_published_at(unknown_texts[0])
    if invalid_date_texts:
        if fallback.status is DateStatus.KNOWN:
            return _conflicting_date()
        return _unknown_published_at(invalid_date_texts[0])
    return fallback


def _deadline_candidates(
    sections: tuple[PostingSectionDraft, ...],
    evidence_by_key: dict[str, EvidenceDraft],
) -> tuple[DeadlineCandidate, ...]:
    candidates: list[DeadlineCandidate] = []
    for section in sections:
        if section.kind is not PostingSectionKind.DEADLINE:
            continue
        for evidence_key in section.evidence_keys:
            text_raw = evidence_by_key[evidence_key].text_excerpt
            for date_match in _ISO_DATE.finditer(text_raw):
                value = date_match.group("value")
                if _is_calendar_iso_date(value):
                    candidates.append(
                        DeadlineCandidate(
                            value=value,
                            raw_text=text_raw,
                            evidence_keys=(evidence_key,),
                        )
                    )
    return tuple(candidates)


def _has_invalid_deadline_date(
    sections: tuple[PostingSectionDraft, ...],
    evidence_by_key: dict[str, EvidenceDraft],
) -> bool:
    return any(
        not _is_calendar_iso_date(date_match.group("value"))
        for section in sections
        if section.kind is PostingSectionKind.DEADLINE
        for evidence_key in section.evidence_keys
        for date_match in _ISO_DATE.finditer(evidence_by_key[evidence_key].text_excerpt)
    )


def _is_calendar_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _conflicting_date() -> DateValue:
    return DateValue(
        status=DateStatus.CONFLICTING,
        raw_text=None,
        value=None,
        precision=None,
        timezone=None,
    )


def _deadline_value(
    candidates: tuple[DeadlineCandidate, ...],
    has_invalid_calendar_date: bool,
) -> DateValue:
    if not candidates:
        return DateValue(
            status=DateStatus.UNKNOWN,
            raw_text=None,
            value=None,
            precision=None,
            timezone=None,
        )
    if not has_invalid_calendar_date and len({candidate.value for candidate in candidates}) == 1:
        candidate = candidates[0]
        return DateValue(
            status=DateStatus.KNOWN,
            raw_text=candidate.raw_text,
            value=candidate.value,
            precision=DatePrecision.DATE,
            timezone=None,
        )
    return DateValue(
        status=DateStatus.CONFLICTING,
        raw_text=None,
        value=None,
        precision=None,
        timezone=None,
    )


def validate_evidence_locator(document: str, evidence: EvidenceDraft) -> bool:
    """Resolve a supported locator and verify its exact excerpt."""

    if evidence.locator.kind is LocatorKind.NORMALIZED_TEXT:
        start = evidence.locator.start
        end = evidence.locator.end
        return (
            start is not None
            and end is not None
            and 0 <= start < end <= len(document)
            and document[start:end] == evidence.text_excerpt
        )
    if evidence.locator.kind is LocatorKind.JSON_POINTER:
        return _validate_json_pointer_locator(document, evidence)
    if evidence.locator.kind is not LocatorKind.XPATH:
        return False
    try:
        matches = Selector(text=document, type="html").xpath(evidence.locator.value)
        return len(matches) == 1 and _visible_text(matches[0]) == evidence.text_excerpt
    except (RecursionError, TypeError, ValueError, _ExtractionLimitExceeded):
        return False


def _validate_json_pointer_locator(document: str, evidence: EvidenceDraft) -> bool:
    try:
        payload = _parse_json_document(document)
    except (json.JSONDecodeError, RecursionError, ValueError):
        matches = sum(
            _json_pointer_matches(payload, evidence.locator.value, evidence.text_excerpt)
            for payload in _embedded_json_payloads(document)
        )
        return matches == 1
    return _json_pointer_matches(payload, evidence.locator.value, evidence.text_excerpt)


def _embedded_json_payloads(document: str) -> tuple[object, ...]:
    try:
        document_selector = Selector(text=document, type="html")
        scripts = _embedded_json_scripts(document_selector)
    except (RecursionError, TypeError, ValueError):
        return ()
    payloads: list[object] = []
    for script in scripts:
        raw_payload = script.xpath("string()").get()
        if not isinstance(raw_payload, str):
            continue
        try:
            payloads.append(_parse_json_document(raw_payload))
        except (json.JSONDecodeError, RecursionError, ValueError):
            continue
    return tuple(payloads)


def _json_pointer_matches(payload: object, pointer: str, text_excerpt: str) -> bool:
    resolved = _resolve_json_pointer(payload, pointer)
    return isinstance(resolved, str) and resolved == text_excerpt


def _resolve_json_pointer(payload: object, pointer: str) -> object:
    if not pointer or not pointer.startswith("/"):
        return _JSON_POINTER_MISSING
    value = payload
    for encoded_token in pointer[1:].split("/"):
        token = _decode_json_pointer_token(encoded_token)
        if token is None:
            return _JSON_POINTER_MISSING
        if isinstance(value, dict):
            mapping = cast(dict[str, object], value)
            if token not in mapping:
                return _JSON_POINTER_MISSING
            value = mapping[token]
            continue
        if isinstance(value, list):
            items = cast(list[object], value)
            index = _canonical_json_array_index(token, len(items))
            if index is None:
                return _JSON_POINTER_MISSING
            value = items[index]
            continue
        return _JSON_POINTER_MISSING
    return value


def _decode_json_pointer_token(token: str) -> str | None:
    decoded: list[str] = []
    index = 0
    while index < len(token):
        character = token[index]
        if character != "~":
            decoded.append(character)
            index += 1
            continue
        if index + 1 >= len(token):
            return None
        escaped = token[index + 1]
        if escaped == "0":
            decoded.append("~")
        elif escaped == "1":
            decoded.append("/")
        else:
            return None
        index += 2
    return "".join(decoded)


def _canonical_json_array_index(token: str, length: int) -> int | None:
    if token == "0":
        return 0 if length else None
    if (
        not token
        or token[0] == "0"
        or not all("0" <= character <= "9" for character in token)
        or len(token) > len(str(length))
    ):
        return None
    index = int(token)
    return index if index < length else None


def _failed_result(
    representation: Representation,
    content_hash: str | None,
    limitation: str,
) -> StaticParseResult:
    return StaticParseResult(
        content_hash=content_hash,
        hash_profile_version=HASH_PROFILE_VERSION,
        parser_version=PARSER_VERSION,
        representation=representation,
        extraction_status=ExtractionStatus.FAILED,
        evidence=(),
        sections=(),
        published_at=_unknown_published_at(None),
        limitations=(limitation,),
    )


def _unknown_published_at(raw_text: str | None) -> DateValue:
    return DateValue(
        status=DateStatus.UNKNOWN,
        raw_text=raw_text,
        value=None,
        precision=None,
        timezone=None,
    )


def _build_candidates(
    block_nodes: list[Selector],
    source_order_by_element: dict[object, int],
    output_budget: _OutputBudget,
) -> tuple[tuple[EvidenceDraft, ...], tuple[_SectionCandidate, ...], str | None]:
    evidence: list[EvidenceDraft] = []
    sections: list[_SectionCandidate] = []
    active_heading: str | None = None
    active_heading_key: str | None = None
    active_keys: list[str] = []
    published_key: str | None = None
    work_budget = _WorkBudget(_MAX_VISIBLE_TEXT_VISITS)

    def finish_section() -> None:
        if active_keys:
            sections.append(
                _SectionCandidate(
                    heading_raw=active_heading,
                    heading_evidence_key=active_heading_key,
                    evidence_keys=tuple(active_keys),
                    order=len(sections),
                )
            )

    for node in block_nodes:
        excerpt = _visible_text(node, work_budget)
        if not excerpt:
            continue
        tag_name = _tag_name(node)
        if tag_name == "strong" and _approved_standalone_heading_kind(excerpt) is None:
            continue
        if tag_name in _HEADING_TAGS:
            finish_section()
            active_keys = []
            active_heading = excerpt

        locator = Locator(
            kind=LocatorKind.XPATH,
            value=_absolute_xpath(node),
            normalization_version=None,
            start=None,
            end=None,
        )
        output_budget.consume_text(excerpt)
        output_budget.consume_text(locator.value)
        if active_heading is not None:
            output_budget.consume_text(active_heading)
        evidence_key = _stable_key(
            "evidence",
            locator.kind.value,
            locator.value,
            excerpt,
            _ORIGIN_KIND,
        )
        draft = EvidenceDraft(
            evidence_key=evidence_key,
            section_title=active_heading,
            text_excerpt=excerpt,
            locator=locator,
            chunk_order=source_order_by_element[node.root],
            origin_kind=_ORIGIN_KIND,
        )
        evidence.append(draft)
        active_keys.append(evidence_key)
        if tag_name in _HEADING_TAGS:
            active_heading_key = evidence_key
        if node.xpath("string(@data-field)").get() == "posting-date":
            published_key = evidence_key

    finish_section()
    return tuple(evidence), tuple(sections), published_key


def _absolute_xpath(node: Selector) -> str:
    path: str = node.root.getroottree().getpath(node.root)
    return path


def _tag_name(node: Selector) -> str:
    return str(node.root.tag).lower()


def _document_positions(root: _HtmlElement) -> dict[object, int]:
    positions: dict[object, int] = {}
    stack: list[tuple[_HtmlElement, int]] = [(root, 0)]
    visited = 0
    source_order = 0
    while stack:
        element, depth = stack.pop()
        if depth > _MAX_DOM_DEPTH:
            raise _ExtractionLimitExceeded("dom_depth_limit_exceeded")
        visited += 1
        if visited > _MAX_DOM_ELEMENTS:
            raise _ExtractionLimitExceeded("dom_element_limit_exceeded")
        if isinstance(element.tag, str):
            positions[element] = source_order
            source_order += 1
        children = list(element)
        stack.extend((child, depth + 1) for child in reversed(children))
    return positions


def _visible_text(node: Selector, work_budget: _WorkBudget | None = None) -> str:
    parts: list[str] = []
    budget = work_budget or _WorkBudget(_MAX_DOM_ELEMENTS)
    _collect_visible_text(cast(_HtmlElement, node.root), parts, budget)
    return " ".join("".join(parts).split())


def _collect_visible_text(
    root: _HtmlElement,
    parts: list[str],
    work_budget: _WorkBudget,
) -> None:
    stack: list[_HtmlElement | _TextToken] = [root]
    while stack:
        item = stack.pop()
        if isinstance(item, _TextToken):
            parts.append(item.value)
            continue
        work_budget.consume()
        if _is_statically_hidden(item):
            continue
        events: list[_HtmlElement | _TextToken] = []
        if item.text:
            events.append(_TextToken(item.text))
        for child in item:
            if not _is_statically_hidden(child):
                is_boundary = str(child.tag).lower() in _TEXT_BOUNDARY_TAGS
                if is_boundary:
                    events.append(_TextToken(" "))
                events.append(child)
                if is_boundary:
                    events.append(_TextToken(" "))
            if child.tail:
                events.append(_TextToken(child.tail))
        stack.extend(reversed(events))


def _is_statically_hidden(element: _HtmlElement) -> bool:
    if not isinstance(element.tag, str):
        return True
    tag = element.tag.lower()
    if tag in _IGNORED_TAGS or element.get("hidden") is not None:
        return True
    if (element.get("aria-hidden") or "").strip().casefold() == "true":
        return True
    style = re.sub(r"\s+", "", (element.get("style") or "").casefold())
    return _HIDDEN_STYLE.search(style) is not None


def _validate_indexed_locator(
    locator_index: dict[str, str],
    evidence: EvidenceDraft,
) -> bool:
    return (
        evidence.locator.kind is LocatorKind.XPATH
        and locator_index.get(evidence.locator.value) == evidence.text_excerpt
    )


def _finalize_sections(
    candidates: tuple[_SectionCandidate, ...],
    evidence_by_key: dict[str, EvidenceDraft],
    output_budget: _OutputBudget,
) -> tuple[ExtractedSectionDraft, ...]:
    sections: list[ExtractedSectionDraft] = []
    for candidate in candidates:
        valid_keys = tuple(key for key in candidate.evidence_keys if key in evidence_by_key)
        if not valid_keys:
            continue
        excerpts = tuple(evidence_by_key[key].text_excerpt for key in valid_keys)
        heading_raw = (
            candidate.heading_raw
            if candidate.heading_evidence_key is None
            or candidate.heading_evidence_key in evidence_by_key
            else None
        )
        for excerpt in excerpts:
            output_budget.consume_text(excerpt)
        output_budget.consume_bytes(max(0, len(excerpts) - 1))
        if heading_raw is not None:
            output_budget.consume_text(heading_raw)
        text_raw = "\n".join(excerpts)
        sections.append(
            ExtractedSectionDraft(
                section_key=_stable_key(
                    "section",
                    "\0".join(valid_keys),
                    text_raw,
                    str(candidate.order),
                ),
                heading_raw=heading_raw,
                text_raw=text_raw,
                evidence_keys=valid_keys,
                order=candidate.order,
            )
        )
    return tuple(sections)


def _sanitize_section_titles(
    evidence: tuple[EvidenceDraft, ...],
    sections: tuple[_SectionCandidate, ...],
    valid_keys: set[str],
) -> tuple[EvidenceDraft, ...]:
    safe_titles: dict[str, str | None] = {}
    for section in sections:
        safe_heading = (
            section.heading_raw
            if section.heading_evidence_key is None or section.heading_evidence_key in valid_keys
            else None
        )
        for evidence_key in section.evidence_keys:
            if evidence_key in valid_keys:
                safe_titles[evidence_key] = safe_heading
    return tuple(
        replace(item, section_title=safe_titles.get(item.evidence_key)) for item in evidence
    )


def _stable_key(prefix: str, *parts: str) -> str:
    payload = "\0".join(parts).encode()
    return f"{prefix}:{sha256(payload).hexdigest()}"
