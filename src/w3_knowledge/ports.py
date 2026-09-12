"""W3가 의존하는 경계. 이 포트에는 fetch·다운로드 API가 없다."""

from __future__ import annotations

from typing import Protocol

from .models import (
    ClaimCandidate,
    ExecutionContext,
    PolicyDecision,
    RequirementCandidate,
    SourceInput,
    ValidationCheck,
)


class PolicyPort(Protocol):
    def decide(self, *, context: ExecutionContext, operation: str) -> PolicyDecision: ...


class RetainedTextPort(Protocol):
    def can_use(self, *, context: ExecutionContext, source: SourceInput) -> PolicyDecision: ...


class ExtractionPort(Protocol):
    def extract_claims(self, *, source: SourceInput) -> tuple[ClaimCandidate, ...]: ...

    def extract_requirements(self, *, source: SourceInput) -> tuple[RequirementCandidate, ...]: ...


class SemanticValidationPort(Protocol):
    def validate_claim(self, candidate: ClaimCandidate) -> tuple[ValidationCheck, ...]: ...

    def validate_requirement(
        self, candidate: RequirementCandidate
    ) -> tuple[ValidationCheck, ...]: ...


class AdditionalVerificationPort(Protocol):
    def verify_claim(self, candidate: ClaimCandidate) -> tuple[ValidationCheck, ...]: ...

    def verify_requirement(
        self, candidate: RequirementCandidate
    ) -> tuple[ValidationCheck, ...]: ...
