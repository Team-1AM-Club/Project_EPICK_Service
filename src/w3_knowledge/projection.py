"""소비자별로 안전한 KnowledgeBundle을 투영한다."""

from __future__ import annotations

from .models import Audience, ExecutionContext, KnowledgeBundle, PolicyDecision, Purpose
from .ports import PolicyPort
from .relations import deduplicate_claims


class ProjectionDenied(PermissionError):
    pass


def project_bundle(
    *, bundle: KnowledgeBundle, context: ExecutionContext, policy: PolicyPort
) -> KnowledgeBundle:
    if policy.decide(context=context, operation="project_bundle") != PolicyDecision.ALLOW:
        raise ProjectionDenied("결과 반환 권한이 없습니다.")
    if context.mode.value == "LIVE" and context.purpose != Purpose.PRODUCTION_STRUCTURE:
        raise ProjectionDenied("LIVE 결과는 PRODUCTION_STRUCTURE 목적에서만 반환합니다.")
    if (
        context.audience == Audience.W4_KNOWLEDGE
        and context.purpose == Purpose.PRODUCTION_STRUCTURE
    ):
        if bundle.mode.value != "LIVE" or bundle.purpose != Purpose.PRODUCTION_STRUCTURE:
            raise ProjectionDenied("운영 소비자는 합성·Mock·진단 결과를 사용할 수 없습니다.")
    claims = deduplicate_claims(bundle.claims)
    requirements = bundle.requirements
    evidences = bundle.evidences
    if context.audience == Audience.W4_KNOWLEDGE:
        claims = tuple(item for item in claims if item.usage_status.value == "USABLE")
        requirements = tuple(item for item in requirements if item.usage_status.value == "USABLE")
        referenced_evidence_ids = {
            evidence_id
            for record in (*claims, *requirements)
            for evidence_id in record.evidence_ids
        }
        evidences = tuple(item for item in evidences if item.evidence_id in referenced_evidence_ids)
    return bundle.model_copy(
        update={"evidences": evidences, "claims": claims, "requirements": requirements}
    )
