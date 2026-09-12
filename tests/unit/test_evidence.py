from w3_knowledge.evidence import digest_text, evidence_checks, make_evidence
from w3_knowledge.models import IntegrityAssertion
from tests.support.factories import source_with_text


def test_evidence_checks_pass_for_matching_text_digest_and_locator() -> None:
    artifact = source_with_text("alpha\r\nbeta").artifacts[0]
    evidence = make_evidence(
        evidence_id="evidence-a", artifact=artifact, start_offset=7, end_offset=11
    )
    assert all(check.status.value == "PASS" for check in evidence_checks(evidence, artifact))


def test_evidence_checks_distinguish_missing_expected_digest() -> None:
    artifact = source_with_text().artifacts[0].model_copy(update={"expected_integrity": None})
    evidence = make_evidence(
        evidence_id="evidence-a", artifact=artifact, start_offset=0, end_offset=2
    )
    assert any(
        check.code == "EXPECTED_DIGEST_MISSING" and check.status.value == "PENDING"
        for check in evidence_checks(evidence, artifact)
    )


def test_evidence_checks_fail_for_changed_excerpt() -> None:
    artifact = source_with_text().artifacts[0]
    evidence = make_evidence(
        evidence_id="evidence-a", artifact=artifact, start_offset=0, end_offset=2
    ).model_copy(update={"excerpt": "xx"})
    assert any(
        check.code == "EXCERPT_MISMATCH" and check.status.value == "FAIL"
        for check in evidence_checks(evidence, artifact)
    )


def test_evidence_checks_use_excerpt_digest_for_excerpt_scope() -> None:
    artifact = (
        source_with_text("alpha\r\nbeta")
        .artifacts[0]
        .model_copy(
            update={
                "expected_integrity": IntegrityAssertion(
                    digest=digest_text("alpha"),
                    scope="excerpt",
                )
            }
        )
    )
    evidence = make_evidence(
        evidence_id="evidence-a", artifact=artifact, start_offset=0, end_offset=5
    )

    checks = evidence_checks(evidence, artifact)

    assert evidence.observed_integrity is not None
    assert evidence.observed_integrity.scope == "excerpt"
    assert any(check.code == "DIGEST_MATCH" and check.status.value == "PASS" for check in checks)


def test_upstream_integrity_report_does_not_promote_current_validation() -> None:
    artifact = (
        source_with_text("retained excerpt")
        .artifacts[0]
        .model_copy(
            update={
                "expected_integrity": None,
                "upstream_integrity": (
                    IntegrityAssertion(
                        digest=digest_text("original unretained document"),
                        reported_by="UPSTREAM_REPORTED",
                    ),
                ),
            }
        )
    )
    evidence = make_evidence(
        evidence_id="evidence-a",
        artifact=artifact,
        start_offset=0,
        end_offset=len("retained excerpt"),
    )

    checks = evidence_checks(evidence, artifact)

    assert any(
        check.code == "UPSTREAM_INTEGRITY_REPORTED"
        and check.status.value == "NOT_REQUIRED"
        and check.reported_by == "UPSTREAM_REPORTED"
        for check in checks
    )
    assert any(
        check.code == "EXPECTED_DIGEST_MISSING" and check.status.value == "PENDING"
        for check in checks
    )
