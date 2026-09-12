from w3_knowledge.evidence import digest_text
from w3_knowledge.models import Audience, CheckStatus, ClaimCandidate, ValidationCheck
from w3_knowledge.projection import project_bundle
from w3_knowledge.service import W3Ports, structure
from tests.support.fakes import (
    FakeAdditionalVerification,
    FakeExtraction,
    FakePolicy,
    FakeRetainedText,
    FakeSemanticValidation,
)
from tests.support.factories import request, source_with_text


def test_w4_receives_only_verified_usable_claims() -> None:
    candidate = ClaimCandidate(
        candidate_id="claim-a", statement="Synthetic", evidence_ids=("evidence-synthetic-artifact",)
    )
    ports = W3Ports(
        FakePolicy(),
        FakeRetainedText(),
        FakeExtraction(claims=(candidate,)),
        FakeSemanticValidation(),
        FakeAdditionalVerification(),
    )
    response = structure(request(), ports)
    context = request().context.model_copy(update={"audience": Audience.W4_KNOWLEDGE})
    bundle = project_bundle(bundle=response.bundle, context=context, policy=FakePolicy())
    assert len(bundle.claims) == 1
    assert bundle.claims[0].evidence_ids


def test_not_assessed_is_exposed_as_a_limit_not_a_negative() -> None:
    response = structure(
        request(),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(),
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )
    assert response.bundle.requirement_presence.value == "NONE_IN_EXAMINED_SCOPE"


def test_as504_w4_projection_omits_evidence_referenced_only_by_blocked_claim() -> None:
    source = source_with_text()
    hidden_artifact = source.artifacts[0].model_copy(
        update={
            "artifact_id": "hidden-artifact",
            "text": "Hidden synthetic text.",
            "expected_integrity": source.artifacts[0].expected_integrity.model_copy(
                update={"digest": digest_text("Hidden synthetic text.")}
            ),
        }
    )
    source = source.model_copy(update={"artifacts": (*source.artifacts, hidden_artifact)})
    public = ClaimCandidate(
        candidate_id="claim-public",
        statement="Public",
        evidence_ids=("evidence-synthetic-artifact",),
    )
    blocked = ClaimCandidate(
        candidate_id="claim-blocked",
        statement="Blocked",
        evidence_ids=("evidence-hidden-artifact",),
    )

    class SelectiveSemanticValidation:
        def validate_claim(self, candidate: ClaimCandidate) -> tuple[ValidationCheck, ...]:
            status = (
                CheckStatus.FAIL if candidate.candidate_id == "claim-blocked" else CheckStatus.PASS
            )
            return (
                ValidationCheck(
                    check_id="semantic-claim", status=status, code="SEMANTIC", message="synthetic"
                ),
            )

        def validate_requirement(self, candidate: ClaimCandidate) -> tuple[ValidationCheck, ...]:
            return ()

    response = structure(
        request(source),
        W3Ports(
            FakePolicy(),
            FakeRetainedText(),
            FakeExtraction(claims=(public, blocked)),
            SelectiveSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )
    context = request().context.model_copy(update={"audience": Audience.W4_KNOWLEDGE})
    bundle = project_bundle(bundle=response.bundle, context=context, policy=FakePolicy())

    assert [claim.candidate_id for claim in bundle.claims] == ["claim-public"]
    assert [evidence.evidence_id for evidence in bundle.evidences] == [
        "evidence-synthetic-artifact"
    ]
