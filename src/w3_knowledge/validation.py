"""추출 후보를 근거·의미·추가 검증 결과와 결합한다."""

from __future__ import annotations

from .models import CheckStatus, VerificationStatus


def verification_status(statuses: tuple[CheckStatus, ...]) -> VerificationStatus:
    if CheckStatus.FAIL in statuses:
        return VerificationStatus.FAILED
    if CheckStatus.PENDING in statuses:
        return VerificationStatus.PENDING
    return VerificationStatus.VERIFIED


def usage_status(status: VerificationStatus):
    from .models import UsageStatus

    if status == VerificationStatus.VERIFIED:
        return UsageStatus.USABLE
    if status == VerificationStatus.PENDING:
        return UsageStatus.RESTRICTED
    return UsageStatus.BLOCKED


def candidate_has_evidence(
    candidate_evidence_ids: tuple[str, ...], available_ids: set[str]
) -> bool:
    return bool(candidate_evidence_ids) and all(
        item in available_ids for item in candidate_evidence_ids
    )
