from __future__ import annotations

from dataclasses import dataclass, field

from w3_knowledge.models import (
    CheckStatus,
    ClaimCandidate,
    ExecutionContext,
    PolicyDecision,
    RequirementCandidate,
    SourceInput,
    ValidationCheck,
)


@dataclass
class FakePolicy:
    decision: PolicyDecision = PolicyDecision.ALLOW
    calls: list[str] = field(default_factory=list)

    def decide(self, *, context: ExecutionContext, operation: str) -> PolicyDecision:
        self.calls.append(operation)
        return self.decision


@dataclass
class FakeRetainedText:
    decision: PolicyDecision = PolicyDecision.ALLOW

    def can_use(self, *, context: ExecutionContext, source: SourceInput) -> PolicyDecision:
        return self.decision


@dataclass
class FakeExtraction:
    claims: tuple[ClaimCandidate, ...] = ()
    requirements: tuple[RequirementCandidate, ...] = ()
    calls: int = 0

    def extract_claims(self, *, source: SourceInput) -> tuple[ClaimCandidate, ...]:
        self.calls += 1
        return self.claims

    def extract_requirements(self, *, source: SourceInput) -> tuple[RequirementCandidate, ...]:
        self.calls += 1
        return self.requirements


@dataclass
class FakeSemanticValidation:
    status: CheckStatus = CheckStatus.PASS

    def validate_claim(self, candidate: ClaimCandidate) -> tuple[ValidationCheck, ...]:
        return (
            ValidationCheck(
                check_id="semantic-claim", status=self.status, code="SEMANTIC", message="synthetic"
            ),
        )

    def validate_requirement(self, candidate: RequirementCandidate) -> tuple[ValidationCheck, ...]:
        return (
            ValidationCheck(
                check_id="semantic-requirement",
                status=self.status,
                code="SEMANTIC",
                message="synthetic",
            ),
        )


@dataclass
class FakeAdditionalVerification:
    status: CheckStatus = CheckStatus.NOT_REQUIRED

    def verify_claim(self, candidate: ClaimCandidate) -> tuple[ValidationCheck, ...]:
        return (
            ValidationCheck(
                check_id="additional-claim",
                status=self.status,
                code="ADDITIONAL",
                message="synthetic",
            ),
        )

    def verify_requirement(self, candidate: RequirementCandidate) -> tuple[ValidationCheck, ...]:
        return (
            ValidationCheck(
                check_id="additional-requirement",
                status=self.status,
                code="ADDITIONAL",
                message="synthetic",
            ),
        )
