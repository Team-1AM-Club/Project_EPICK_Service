"""Provenance locator validation contracts."""

from __future__ import annotations

import pytest

from epick_engine.source_collection.contracts import Locator, LocatorKind
from epick_engine.source_collection.parsing import (
    EvidenceDraft,
    validate_evidence_locator,
)


def _evidence(locator: Locator, text_excerpt: str) -> EvidenceDraft:
    return EvidenceDraft(
        evidence_key="evidence",
        section_title=None,
        text_excerpt=text_excerpt,
        locator=locator,
        chunk_order=0,
        origin_kind="static_html",
    )


def _normalized_text_locator(start: int, end: int) -> Locator:
    return Locator(
        kind=LocatorKind.NORMALIZED_TEXT,
        value="normalized-text-v1",
        normalization_version="normalized-text-v1",
        start=start,
        end=end,
    )


def test_normalized_text_locator_uses_unicode_code_point_offsets() -> None:
    document = "a😀bcdef"

    code_point_evidence = _evidence(_normalized_text_locator(2, 3), "b")
    utf8_byte_evidence = _evidence(_normalized_text_locator(5, 6), "b")

    assert validate_evidence_locator(document, code_point_evidence)
    assert not validate_evidence_locator(document, utf8_byte_evidence)


@pytest.mark.parametrize(("start", "end"), [(-1, 1), (1, 1), (0, 4)])
def test_normalized_text_locator_rejects_invalid_bounds(start: int, end: int) -> None:
    locator = Locator.model_construct(
        kind=LocatorKind.NORMALIZED_TEXT,
        value="normalized-text-v1",
        normalization_version="normalized-text-v1",
        start=start,
        end=end,
    )

    assert not validate_evidence_locator("abc", _evidence(locator, "a"))


def test_normalized_text_locator_rejects_excerpt_mismatch() -> None:
    evidence = _evidence(_normalized_text_locator(0, 3), "abd")

    assert not validate_evidence_locator("abcdef", evidence)


@pytest.mark.parametrize("kind", [LocatorKind.CSS, LocatorKind.JSON_POINTER])
def test_non_xpath_non_normalized_locators_fail_closed(kind: LocatorKind) -> None:
    locator = Locator(
        kind=kind,
        value="locator",
        normalization_version=None,
        start=None,
        end=None,
    )

    assert not validate_evidence_locator("<p>Visible</p>", _evidence(locator, "Visible"))
