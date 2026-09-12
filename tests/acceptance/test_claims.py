from w3_knowledge.evidence import digest_text
from w3_knowledge.models import ClaimCandidate, CheckStatus, IntegrityAssertion, SourceInput
from w3_knowledge.service import W3Ports, structure
from tests.support.fakes import (
    FakeAdditionalVerification,
    FakeExtraction,
    FakePolicy,
    FakeRetainedText,
    FakeSemanticValidation,
)
from tests.support.factories import request, source_with_text


def test_as201_verified_claim_has_source_evidence_and_version_path() -> None:
    candidate = ClaimCandidate(
        candidate_id="claim-a",
        statement="Synthetic statement",
        evidence_ids=("evidence-synthetic-artifact",),
        scope="synthetic",
    )
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(candidate,)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )
    claim = response.bundle.claims[0]
    assert claim.verification_status.value == "VERIFIED"
    assert claim.evidence_ids == (response.bundle.evidences[0].evidence_id,)
    assert response.bundle.evidences[0].source_ref.source_version_id == "synthetic-source-v1"


def test_unknown_evidence_reference_cannot_be_verified() -> None:
    candidate = ClaimCandidate(
        candidate_id="claim-a", statement="Synthetic statement", evidence_ids=("evidence-missing",)
    )
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(candidate,)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )
    assert response.bundle.claims[0].verification_status.value == "FAILED"


def test_semantic_pending_keeps_claim_restricted() -> None:
    candidate = ClaimCandidate(
        candidate_id="claim-a",
        statement="Synthetic statement",
        evidence_ids=("evidence-synthetic-artifact",),
    )
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(candidate,)),
            FakeSemanticValidation(CheckStatus.PENDING),
            FakeAdditionalVerification(),
        ),
    )
    assert response.bundle.claims[0].usage_status.value == "RESTRICTED"


def test_as202_does_not_add_absent_claim_attributes() -> None:
    candidate = ClaimCandidate(
        candidate_id="claim-plan",
        statement="The organization plans a future project.",
        evidence_ids=("evidence-synthetic-artifact",),
        subject="Synthetic organization",
        scope="PLAN",
        period="2026",
    )
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(candidate,)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )

    claim = response.bundle.claims[0]
    assert claim.statement == candidate.statement
    assert claim.subject == "Synthetic organization"
    assert claim.scope == "PLAN"
    assert claim.period == "2026"
    assert claim.unit is None
    assert claim.comparison_basis is None


def test_as203_retained_excerpt_with_unreproducible_original_locator_is_restricted() -> None:
    source = source_with_text("retained excerpt only")
    locator = source.artifacts[0].native_locator.model_copy(
        update={
            "locator_type": "external_reference",
            "value": "https://unavailable.invalid/v1#p4",
            "reproducible": False,
        }
    )
    artifact = source.artifacts[0].model_copy(
        update={"native_locator": locator, "expected_integrity": None}
    )
    retained_excerpt_source = SourceInput(source_ref=source.source_ref, artifacts=(artifact,))
    candidate = ClaimCandidate(
        candidate_id="claim-a",
        statement="Synthetic statement",
        evidence_ids=("evidence-synthetic-artifact",),
    )

    response = structure(
        request(retained_excerpt_source),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(candidate,)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )

    evidence = response.bundle.evidences[0]
    claim = response.bundle.claims[0]
    assert evidence.excerpt == "retained excerpt only"
    assert "text" not in evidence.model_dump()
    assert evidence.native_locator == locator
    assert claim.usage_status.value == "RESTRICTED"


def test_as205_pending_additional_verification_keeps_claim_restricted() -> None:
    candidate = ClaimCandidate(
        candidate_id="claim-a",
        statement="Synthetic statement",
        evidence_ids=("evidence-synthetic-artifact",),
    )
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(candidate,)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(CheckStatus.PENDING),
        ),
    )

    claim = response.bundle.claims[0]
    assert claim.verification_status.value == "PENDING"
    assert claim.usage_status.value == "RESTRICTED"


def test_as206_structure_does_not_mutate_source_or_version_reference() -> None:
    source = source_with_text("unchanged source text")
    source_before = source.model_dump()
    candidate = ClaimCandidate(
        candidate_id="claim-a",
        statement="Synthetic statement",
        evidence_ids=("evidence-synthetic-artifact",),
    )

    response = structure(
        request(source),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(candidate,)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )

    assert source.model_dump() == source_before
    assert response.bundle.evidences[0].source_ref == source.source_ref


def test_as208_upstream_integrity_report_without_reproducible_target_is_restricted() -> None:
    source = source_with_text("retained excerpt")
    artifact = source.artifacts[0].model_copy(
        update={
            "expected_integrity": None,
            "upstream_integrity": (
                IntegrityAssertion(
                    digest=digest_text("unretained original"),
                    reported_by="UPSTREAM_REPORTED",
                ),
            ),
        }
    )
    reported_source = SourceInput(source_ref=source.source_ref, artifacts=(artifact,))
    candidate = ClaimCandidate(
        candidate_id="claim-a",
        statement="Synthetic statement",
        evidence_ids=("evidence-synthetic-artifact",),
    )

    response = structure(
        request(reported_source),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(candidate,)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )

    claim = response.bundle.claims[0]
    assert claim.usage_status.value == "RESTRICTED"
    assert any(check.code == "UPSTREAM_INTEGRITY_REPORTED" for check in claim.checks)


def test_as204_claim_with_digest_mismatched_evidence_is_blocked() -> None:
    source = source_with_text("original evidence")
    artifact = source.artifacts[0].model_copy(
        update={"expected_integrity": IntegrityAssertion(digest=digest_text("different evidence"))}
    )
    changed_source = SourceInput(source_ref=source.source_ref, artifacts=(artifact,))
    candidate = ClaimCandidate(
        candidate_id="claim-a",
        statement="Synthetic statement",
        evidence_ids=("evidence-synthetic-artifact",),
    )

    response = structure(
        request(changed_source),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(candidate,)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )

    claim = response.bundle.claims[0]
    assert claim.usage_status.value == "BLOCKED"
    assert any(
        check.code == "DIGEST_MISMATCH" and check.status.value == "FAIL" for check in claim.checks
    )
