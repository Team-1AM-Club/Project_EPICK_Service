from w3_knowledge.models import PolicyDecision
from w3_knowledge.service import W3Ports, structure
from tests.support.fakes import (
    FakeAdditionalVerification,
    FakeExtraction,
    FakePolicy,
    FakeRetainedText,
    FakeSemanticValidation,
)
from tests.support.factories import request


def test_policy_denied_blocks_before_extraction() -> None:
    extraction = FakeExtraction()
    result = structure(
        request(),
        W3Ports(
            FakePolicy(PolicyDecision.DENY),
            FakeRetainedText(),
            extraction,
            FakeSemanticValidation(),
            FakeAdditionalVerification(),
        ),
    )
    assert result.status.value == "PAUSED"
    assert result.errors[0].code.value == "POLICY_DENIED"
    assert extraction.calls == 0
