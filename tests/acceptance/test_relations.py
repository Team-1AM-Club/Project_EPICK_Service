from w3_knowledge.models import (
    CheckStatus,
    Claim,
    ClaimCandidate,
    UsageStatus,
    ValidationCheck,
    VerificationStatus,
)
from w3_knowledge.relations import can_link_relation, same_origin
from w3_knowledge.service import W3Ports, structure
from tests.support.fakes import (
    FakeAdditionalVerification,
    FakeExtraction,
    FakePolicy,
    FakeRetainedText,
    FakeSemanticValidation,
)
from tests.support.factories import request


def _claim(statement: str, evidence: tuple[str, ...], period: str = "2026") -> Claim:
    return Claim(
        candidate_id="claim-a",
        statement=statement,
        evidence_ids=evidence,
        verification_status=VerificationStatus.VERIFIED,
        usage_status=UsageStatus.USABLE,
        checks=(ValidationCheck(check_id="ok", status=CheckStatus.PASS, code="OK", message="ok"),),
        period=period,
    )


def test_same_text_without_same_evidence_is_not_same_origin() -> None:
    assert not same_origin(_claim("same", ("evidence-a",)), _claim("same", ("evidence-b",)))


def test_origin_link_requires_same_scope_period_unit_and_basis() -> None:
    assert not can_link_relation(
        _claim("same", ("evidence-a",), "2025"), _claim("same", ("evidence-a",), "2026")
    )


def test_as501_structure_keeps_one_claim_for_same_verified_origin() -> None:
    first = ClaimCandidate(
        candidate_id="claim-first",
        statement="same",
        evidence_ids=("evidence-synthetic-artifact",),
        scope="synthetic",
    )
    duplicate = ClaimCandidate(
        candidate_id="claim-duplicate",
        statement="same",
        evidence_ids=("evidence-synthetic-artifact",),
        scope="synthetic",
    )
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(first, duplicate)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )

    assert [claim.candidate_id for claim in response.bundle.claims] == ["claim-first"]


def test_as503_structure_keeps_same_origin_claims_with_different_periods() -> None:
    first = ClaimCandidate(
        candidate_id="claim-2025",
        statement="same",
        evidence_ids=("evidence-synthetic-artifact",),
        scope="synthetic",
        period="2025",
    )
    later = ClaimCandidate(
        candidate_id="claim-2026",
        statement="same",
        evidence_ids=("evidence-synthetic-artifact",),
        scope="synthetic",
        period="2026",
    )
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(first, later)),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )

    assert [claim.candidate_id for claim in response.bundle.claims] == ["claim-2025", "claim-2026"]


def test_as506_structure_keeps_same_origin_claims_with_different_validation_results() -> None:
    accepted = ClaimCandidate(
        candidate_id="claim-accepted",
        statement="same",
        evidence_ids=("evidence-synthetic-artifact",),
        scope="synthetic",
    )
    rejected = ClaimCandidate(
        candidate_id="claim-rejected",
        statement="same",
        evidence_ids=("evidence-synthetic-artifact",),
        scope="synthetic",
    )

    class SelectiveSemanticValidation:
        def validate_claim(self, candidate: ClaimCandidate) -> tuple[ValidationCheck, ...]:
            status = (
                CheckStatus.FAIL if candidate.candidate_id == "claim-rejected" else CheckStatus.PASS
            )
            return (
                ValidationCheck(
                    check_id="semantic-claim", status=status, code="SEMANTIC", message="synthetic"
                ),
            )

        def validate_requirement(self, candidate: ClaimCandidate) -> tuple[ValidationCheck, ...]:
            return ()

    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(accepted, rejected)),
            SelectiveSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )

    assert [claim.candidate_id for claim in response.bundle.claims] == [
        "claim-accepted",
        "claim-rejected",
    ]
