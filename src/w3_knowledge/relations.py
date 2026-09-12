"""동일 origin과 관계 후보를 보수적으로 판단한다."""

from __future__ import annotations

from hashlib import sha256

from .models import Claim


def deterministic_id(namespace: str, *parts: str) -> str:
    material = "\x1f".join((namespace, *parts)).encode("utf-8")
    return f"{namespace}:{sha256(material).hexdigest()[:24]}"


def same_origin(left: Claim, right: Claim) -> bool:
    """원문 문구가 아니라 SourceVersion과 Evidence가 모두 같을 때만 true."""
    if left.statement != right.statement:
        return False
    return set(left.evidence_ids) == set(right.evidence_ids) and bool(left.evidence_ids)


def can_link_relation(left: Claim, right: Claim) -> bool:
    if not same_origin(left, right):
        return False
    return (
        left.scope == right.scope
        and left.period == right.period
        and left.unit == right.unit
        and left.comparison_basis == right.comparison_basis
    )


def can_deduplicate_claim(left: Claim, right: Claim) -> bool:
    return (
        can_link_relation(left, right)
        and left.verification_status == right.verification_status
        and left.usage_status == right.usage_status
        and left.checks == right.checks
    )


def deduplicate_claims(claims: tuple[Claim, ...]) -> tuple[Claim, ...]:
    output: list[Claim] = []
    for claim in claims:
        if not any(can_deduplicate_claim(existing, claim) for existing in output):
            output.append(claim)
    return tuple(output)
