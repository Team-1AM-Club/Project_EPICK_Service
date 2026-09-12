from w3_knowledge.evidence import evidence_checks, make_evidence
from tests.support.factories import source_with_text


def test_unreproducible_native_locator_is_pending_not_pass() -> None:
    artifact = (
        source_with_text()
        .artifacts[0]
        .model_copy(
            update={
                "native_locator": source_with_text()
                .artifacts[0]
                .native_locator.model_copy(update={"reproducible": False})
            }
        )
    )
    evidence = make_evidence(
        evidence_id="evidence-a", artifact=artifact, start_offset=0, end_offset=2
    )
    assert any(
        item.code == "NATIVE_LOCATOR_UNCONFIRMED" and item.status.value == "PENDING"
        for item in evidence_checks(evidence, artifact)
    )


def test_as207_preserves_unicode_and_crlf_excerpt_offsets_separately_from_origin_locator() -> None:
    text = "before\r\n가😀after"
    excerpt = "가😀"
    source = source_with_text(text)
    locator = source.artifacts[0].native_locator.model_copy(update={"reproducible": False})
    artifact = source.artifacts[0].model_copy(update={"native_locator": locator})
    start_offset = text.index(excerpt)
    end_offset = start_offset + len(excerpt)

    evidence = make_evidence(
        evidence_id="evidence-a",
        artifact=artifact,
        start_offset=start_offset,
        end_offset=end_offset,
    )
    checks = evidence_checks(evidence, artifact)

    assert evidence.excerpt == excerpt
    assert evidence.start_offset == start_offset
    assert evidence.end_offset == end_offset
    assert evidence.native_locator == locator
    assert any(item.code == "EXCERPT_MATCH" and item.status.value == "PASS" for item in checks)
    assert any(
        item.code == "NATIVE_LOCATOR_UNCONFIRMED" and item.status.value == "PENDING"
        for item in checks
    )
