from __future__ import annotations

from pathlib import Path

import pytest

import epick_engine.source_collection.parsing as parsing_module
from epick_engine.source_collection.collector import StaticResponseCandidate
from epick_engine.source_collection.contracts import (
    DateStatus,
    ExtractionStatus,
    Locator,
    LocatorKind,
)
from epick_engine.source_collection.parsing import (
    HASH_PROFILE_VERSION,
    PARSER_VERSION,
    EvidenceDraft,
    compute_content_hash,
    extract_static_candidate,
    normalize_hash_document,
    validate_evidence_locator,
)
from epick_engine.source_collection.policy import (
    Representation,
    UntrustedDocument,
    ValidatedTarget,
)

_FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "synthetic_sources" / "static_posting.html"
_TARGET = ValidatedTarget(
    url="https://synthetic-meridian-careers.test/jobs/static-posting",
    hostname="synthetic-meridian-careers.test",
    port=443,
    resolved_addresses=frozenset({"8.8.8.8"}),
)


def _candidate(
    document: str,
    representation: Representation = Representation.HTML,
) -> StaticResponseCandidate:
    document_size = len(document.encode("utf-8"))
    return StaticResponseCandidate(
        final_target=_TARGET,
        representation=representation,
        document=UntrustedDocument(document),
        http_status=200,
        raw_size=document_size,
        decompressed_size=document_size,
    )


def _fixture_document() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8")


def test_content_hash_uses_canonical_representation_separator_and_utf8_bytes() -> None:
    document = "Role\nCafé"

    assert HASH_PROFILE_VERSION == "epick-response-sha256-v1"
    assert compute_content_hash(Representation.HTML, document) == (
        "6de261cb8c31e56db4390184485ab6a3dbf4fb88d95d081302e66406cd356169"
    )


def test_hash_normalization_only_unifies_line_endings() -> None:
    canonical = "Café requires Python and SQL.\nApply by 2030-02-01."

    assert normalize_hash_document(canonical.replace("\n", "\r\n")) == canonical
    assert normalize_hash_document(canonical.replace("\n", "\r")) == canonical
    assert compute_content_hash(Representation.HTML, canonical.replace("\n", "\r\n")) == (
        compute_content_hash(Representation.HTML, canonical)
    )
    assert compute_content_hash(Representation.HTML, canonical.replace("\n", "\r")) == (
        compute_content_hash(Representation.HTML, canonical)
    )


def test_content_hash_is_domain_separated_by_representation() -> None:
    document = '{"role":"Platform Engineer"}'

    assert compute_content_hash(Representation.HTML, document) != compute_content_hash(
        Representation.JSON,
        document,
    )


@pytest.mark.parametrize(
    "changed_document",
    [
        "Cafe\u0301 requires Python and SQL.\nApply by 2030-02-01.",
        "Café requires Python  and SQL.\nApply by 2030-02-01.",
        "Café requires Python and SQL!\nApply by 2030-02-01.",
        "Café requires Python and SQL.\nApply by 2030-02-02.",
        "Café requires Python or SQL.\nApply by 2030-02-01.",
        "Apply by 2030-02-01.\nCafé requires Python and SQL.",
    ],
)
def test_content_hash_preserves_material_document_differences(changed_document: str) -> None:
    canonical = "Café requires Python and SQL.\nApply by 2030-02-01."

    assert compute_content_hash(Representation.HTML, changed_document) != compute_content_hash(
        Representation.HTML,
        canonical,
    )


def test_fixture_extraction_emits_valid_evidence_in_source_order() -> None:
    document = _fixture_document()

    parsed = extract_static_candidate(_candidate(document))

    assert PARSER_VERSION == "epick-static-evidence-v3"
    assert parsed.extraction_status is ExtractionStatus.COMPLETE
    assert parsed.evidence
    assert all(validate_evidence_locator(document, evidence) for evidence in parsed.evidence)

    chunk_orders = [evidence.chunk_order for evidence in parsed.evidence]
    section_orders = [section.order for section in parsed.sections]
    evidence_keys = {evidence.evidence_key for evidence in parsed.evidence}
    assert chunk_orders == sorted(chunk_orders)
    assert len(chunk_orders) == len(set(chunk_orders))
    assert section_orders == sorted(section_orders)
    assert len(section_orders) == len(set(section_orders))
    assert all(set(section.evidence_keys) <= evidence_keys for section in parsed.sections)


def test_fixture_posting_date_remains_explicitly_unknown() -> None:
    parsed = extract_static_candidate(_candidate(_fixture_document()))

    assert parsed.published_at.status is DateStatus.UNKNOWN
    assert parsed.published_at.raw_text == "The posting date is unknown."
    assert parsed.published_at.value is None
    assert parsed.published_at.precision is None
    assert parsed.published_at.timezone is None


def test_fixture_preserves_requirement_logic_and_date_sentences_in_order() -> None:
    parsed = extract_static_candidate(_candidate(_fixture_document()))
    excerpts = [evidence.text_excerpt for evidence in parsed.evidence]
    expected_in_order = [
        "AWS experience is required.",
        (
            "GCP is mentioned only as a general cloud-platform example and is not "
            "a mandatory requirement."
        ),
        "You must demonstrate Python and SQL experience.",
        "You must demonstrate Kubernetes or Docker experience.",
        ("The same experience may satisfy the AWS requirement and the Python and SQL requirement."),
        "Applications close on 2030-02-01.",
        "A secondary synthetic notice says applications close on 2030-02-02.",
    ]

    assert [excerpts.index(text) for text in expected_in_order] == sorted(
        excerpts.index(text) for text in expected_in_order
    )


def test_html_extraction_ignores_script_style_and_template_text() -> None:
    document = """
    <html><body>
      <h1>Visible role</h1>
      <p>Visible evidence text.</p>
      <script>Script-only phrase must not be evidence.</script>
      <style>.example::before { content: "Style-only phrase must not be evidence."; }</style>
      <template><p>Template-only phrase must not be evidence.</p></template>
    </body></html>
    """

    parsed = extract_static_candidate(_candidate(document))
    extracted_text = "\n".join(
        [evidence.text_excerpt for evidence in parsed.evidence]
        + [section.heading_raw for section in parsed.sections]
        + [section.text_raw for section in parsed.sections]
    )

    assert parsed.extraction_status is ExtractionStatus.COMPLETE
    assert "Visible evidence text." in extracted_text
    assert "Script-only phrase" not in extracted_text
    assert "Style-only phrase" not in extracted_text
    assert "Template-only phrase" not in extracted_text


def test_text_extraction_preserves_br_as_a_logical_boundary() -> None:
    document = "<html><body><p>Python<br>and SQL or Docker</p></body></html>"

    parsed = extract_static_candidate(_candidate(document))

    assert parsed.extraction_status is ExtractionStatus.COMPLETE
    assert parsed.evidence[0].text_excerpt == "Python and SQL or Docker"
    assert validate_evidence_locator(document, parsed.evidence[0])


def test_statically_hidden_subtrees_are_not_promoted_to_evidence() -> None:
    document = """
    <html><body>
      <h1>Visible role</h1>
      <p>Visible <span hidden>hidden child</span> evidence.</p>
      <p hidden>hidden attribute</p>
      <p aria-hidden="TRUE">hidden aria</p>
      <p style="DISPLAY : none !important">hidden style</p>
      <p style="visibility: hidden">hidden visibility</p>
    </body></html>
    """

    parsed = extract_static_candidate(_candidate(document))
    excerpts = [evidence.text_excerpt for evidence in parsed.evidence]

    assert parsed.extraction_status is ExtractionStatus.COMPLETE
    assert excerpts == ["Visible role", "Visible evidence."]
    assert "external_css_visibility_not_evaluated" in parsed.limitations


def test_default_locator_validation_parses_the_document_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selector_constructions = 0
    original_selector = parsing_module.Selector

    def counting_selector(*args: object, **kwargs: object) -> object:
        nonlocal selector_constructions
        selector_constructions += 1
        return original_selector(*args, **kwargs)

    monkeypatch.setattr(parsing_module, "Selector", counting_selector)

    parsed = extract_static_candidate(
        _candidate("<html><body><p>One</p><p>Two</p><p>Three</p></body></html>")
    )

    assert parsed.extraction_status is ExtractionStatus.COMPLETE
    assert selector_constructions == 1


def test_evidence_candidate_limit_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parsing_module, "_MAX_EVIDENCE_CANDIDATES", 1)

    parsed = extract_static_candidate(_candidate("<html><body><p>One</p><p>Two</p></body></html>"))

    assert parsed.extraction_status is ExtractionStatus.FAILED
    assert parsed.evidence == ()
    assert parsed.sections == ()
    assert parsed.limitations == ("evidence_candidate_limit_exceeded",)


def test_deep_dom_fails_closed_without_recursive_text_traversal() -> None:
    document = (
        "<html><body><p>Evidence"
        + ("<span>" * 600)
        + "deep"
        + ("</span>" * 600)
        + "</p></body></html>"
    )

    parsed = extract_static_candidate(_candidate(document))

    assert parsed.extraction_status is ExtractionStatus.FAILED
    assert parsed.evidence == ()
    assert parsed.sections == ()
    assert parsed.limitations == ("dom_depth_limit_exceeded",)


def test_dom_element_limit_counts_non_evidence_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parsing_module, "_MAX_DOM_ELEMENTS", 4)
    document = "<html><body><div><span>Nested</span></div><p>Evidence</p></body></html>"

    parsed = extract_static_candidate(_candidate(document))

    assert parsed.extraction_status is ExtractionStatus.FAILED
    assert parsed.evidence == ()
    assert parsed.sections == ()
    assert parsed.limitations == ("dom_element_limit_exceeded",)


def test_visible_text_work_limit_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(parsing_module, "_MAX_VISIBLE_TEXT_VISITS", 1)

    parsed = extract_static_candidate(_candidate("<html><body><p>One</p><p>Two</p></body></html>"))

    assert parsed.extraction_status is ExtractionStatus.FAILED
    assert parsed.evidence == ()
    assert parsed.sections == ()
    assert parsed.limitations == ("visible_text_work_limit_exceeded",)


def test_nested_blocks_cannot_amplify_extracted_output_without_bound() -> None:
    document = (
        "<html><body>"
        + ("<ul><li>" * 100)
        + ("x" * 10_000)
        + ("</li></ul>" * 100)
        + "</body></html>"
    )

    parsed = extract_static_candidate(_candidate(document))

    assert parsed.extraction_status is ExtractionStatus.FAILED
    assert parsed.evidence == ()
    assert parsed.sections == ()
    assert parsed.limitations == ("extracted_output_limit_exceeded",)


def test_html_comments_are_not_promoted_to_visible_text() -> None:
    document = "<html><body><p>Visible<!-- hidden comment --> evidence.</p></body></html>"

    parsed = extract_static_candidate(_candidate(document))

    assert parsed.extraction_status is ExtractionStatus.COMPLETE
    assert [item.text_excerpt for item in parsed.evidence] == ["Visible evidence."]


def test_evidence_identity_and_order_do_not_depend_on_candidate_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = "<html><body><p>First</p><p>Second</p></body></html>"
    complete = extract_static_candidate(_candidate(document))
    complete_second = next(
        evidence for evidence in complete.evidence if evidence.text_excerpt == "Second"
    )
    monkeypatch.setattr(parsing_module, "_BLOCK_XPATH", "/html/body/p[2]")

    narrowed = extract_static_candidate(_candidate(document))

    assert narrowed.evidence[0].evidence_key == complete_second.evidence_key
    assert narrowed.evidence[0].chunk_order == complete_second.chunk_order


def test_invalid_unicode_document_returns_failed_without_content_identity() -> None:
    document = "\ud800"
    candidate = StaticResponseCandidate(
        final_target=_TARGET,
        representation=Representation.HTML,
        document=UntrustedDocument(document),
        http_status=200,
        raw_size=1,
        decompressed_size=1,
    )

    parsed = extract_static_candidate(candidate)

    assert parsed.extraction_status is ExtractionStatus.FAILED
    assert parsed.content_hash is None
    assert parsed.limitations == ("invalid_unicode_document",)


def test_invalid_xpath_is_not_accepted_as_evidence() -> None:
    evidence = EvidenceDraft(
        evidence_key="invalid-xpath",
        section_title=None,
        text_excerpt="Visible",
        locator=Locator(
            kind=LocatorKind.XPATH,
            value="//*[",
            normalization_version=None,
            start=None,
            end=None,
        ),
        chunk_order=0,
        origin_kind="static_html",
    )

    assert not validate_evidence_locator("<p>Visible</p>", evidence)


def test_invalid_locator_yields_partial_result_and_omits_its_evidence() -> None:
    rejected_keys: set[str] = set()

    def reject_first_locator(_document: str, evidence: EvidenceDraft) -> bool:
        evidence_key = evidence.evidence_key
        if not rejected_keys:
            rejected_keys.add(evidence_key)
            return False
        return True

    parsed = extract_static_candidate(
        _candidate(_fixture_document()),
        locator_validator=reject_first_locator,
    )

    assert parsed.extraction_status is ExtractionStatus.PARTIAL
    assert rejected_keys
    assert parsed.evidence
    assert all(evidence.evidence_key not in rejected_keys for evidence in parsed.evidence)


def test_invalid_heading_is_not_exposed_by_retained_evidence_or_section() -> None:
    def reject_requirements_heading(
        _document: str,
        evidence: EvidenceDraft,
    ) -> bool:
        return evidence.text_excerpt != "Requirements"

    parsed = extract_static_candidate(
        _candidate(_fixture_document()),
        locator_validator=reject_requirements_heading,
    )

    requirement_evidence = next(
        evidence
        for evidence in parsed.evidence
        if evidence.text_excerpt == "AWS experience is required."
    )
    requirement_section = next(
        section for section in parsed.sections if "AWS experience is required." in section.text_raw
    )
    assert parsed.extraction_status is ExtractionStatus.PARTIAL
    assert requirement_evidence.section_title is None
    assert requirement_section.heading_raw is None


def test_all_invalid_locators_produce_failed_result_without_drafts() -> None:
    parsed = extract_static_candidate(
        _candidate(_fixture_document()),
        locator_validator=lambda _document, _evidence: False,
    )

    assert parsed.extraction_status is ExtractionStatus.FAILED
    assert parsed.evidence == ()
    assert parsed.sections == ()


def test_json_extraction_emits_evidence_and_malformed_json_fails() -> None:
    parsed = extract_static_candidate(
        _candidate(
            '{"role":"Platform Engineer"}',
            representation=Representation.JSON,
        )
    )

    assert parsed.extraction_status is ExtractionStatus.COMPLETE
    assert [
        (item.text_excerpt, item.locator.kind, item.locator.value) for item in parsed.evidence
    ] == [("Platform Engineer", LocatorKind.JSON_POINTER, "/role")]
    assert all(
        validate_evidence_locator('{"role":"Platform Engineer"}', item) for item in parsed.evidence
    )

    malformed = extract_static_candidate(_candidate('{"role":', representation=Representation.JSON))

    assert malformed.extraction_status is ExtractionStatus.FAILED
    assert malformed.evidence == ()
    assert malformed.sections == ()
    assert malformed.limitations == ("malformed_json",)


def test_structureless_html_produces_failed_result() -> None:
    parsed = extract_static_candidate(
        _candidate("<html><body><div>Only container text.</div></body></html>")
    )

    assert parsed.extraction_status is ExtractionStatus.FAILED
    assert parsed.evidence == ()
    assert parsed.sections == ()


def test_repeated_parse_of_fixture_is_stable() -> None:
    candidate = _candidate(_fixture_document())

    assert extract_static_candidate(candidate) == extract_static_candidate(candidate)
