"""W3 구조화 흐름. 정책 검사 후에만 원문 사용·추출·반환을 수행한다."""

from __future__ import annotations

from dataclasses import dataclass

from .adapters.solar import ProviderRateLimited, ProviderResponseError
from .config import RuntimeConfiguration, live_execution_error
from .errors import ErrorCode, SafeError
from .evidence import evidence_checks, make_evidence
from .models import (
    CheckStatus,
    Claim,
    ClaimCandidate,
    Artifact,
    Evidence,
    KnowledgeBundle,
    Limitation,
    PolicyDecision,
    ProcessingStatus,
    Requirement,
    RequirementCandidate,
    RequirementPresenceState,
    SourceInput,
    StructureRequest,
    StructureResponse,
    ValidationCheck,
)
from .ports import (
    AdditionalVerificationPort,
    ExtractionPort,
    PolicyPort,
    RetainedTextPort,
    SemanticValidationPort,
)
from .relations import deduplicate_claims
from .requirements import ConditionTreeError, validate_requirement_candidate
from .review import review_source
from .validation import candidate_has_evidence, usage_status, verification_status


@dataclass(frozen=True)
class W3Ports:
    policy: PolicyPort
    retained_text: RetainedTextPort
    extraction: ExtractionPort
    semantic_validation: SemanticValidationPort
    additional_verification: AdditionalVerificationPort


def structure(
    request: StructureRequest, ports: W3Ports, config: RuntimeConfiguration | None = None
) -> StructureResponse:
    configuration = config or RuntimeConfiguration()
    live_error = live_execution_error(request.context.mode, request.context.purpose, configuration)
    if live_error:
        return _blocked(request, live_error)
    decision = ports.policy.decide(context=request.context, operation="structure")
    if decision != PolicyDecision.ALLOW:
        code = (
            ErrorCode.POLICY_DENIED if decision == PolicyDecision.DENY else ErrorCode.POLICY_UNKNOWN
        )
        return _blocked(request, SafeError(code=code, message="구조화 권한이 확인되지 않았습니다."))

    reviews = []
    all_evidence: list[Evidence] = []
    claims: list[Claim] = []
    requirements: list[Requirement] = []
    limitations: list[Limitation] = []
    errors: list[SafeError] = []
    assessment_possible = False

    for source in request.sources:
        review = review_source(source)
        reviews.append(review)
        limitations.extend(review.limitations)
        if (
            ports.retained_text.can_use(context=request.context, source=source)
            != PolicyDecision.ALLOW
        ):
            limitations.append(
                Limitation(
                    code="RETAINED_TEXT_NOT_ALLOWED",
                    impact="이 SourceVersion은 원문 분석 대상에서 제외되었습니다.",
                    source_ref=source.source_ref,
                )
            )
            continue
        evidence_for_source = _evidence_for_source(source)
        all_evidence.extend(evidence_for_source)
        if not evidence_for_source:
            continue
        assessment_possible = True
        available = {item.evidence_id for item in evidence_for_source}
        artifacts_by_id = {artifact.artifact_id: artifact for artifact in source.artifacts}
        try:
            candidates = ports.extraction.extract_claims(source=source)
            req_candidates = ports.extraction.extract_requirements(source=source)
        except ProviderRateLimited as exc:
            errors.append(
                SafeError(
                    code=ErrorCode.PROVIDER_RATE_LIMITED,
                    message="공급자 제한으로 해당 자료의 추출을 중단했습니다.",
                    retry_after_seconds=exc.retry_after_seconds,
                    source_id=source.source_ref.source_id,
                )
            )
            limitations.append(
                Limitation(
                    code="PROVIDER_RATE_LIMITED",
                    impact="자동 재시도 없이 해당 자료만 미처리로 남았습니다.",
                    source_ref=source.source_ref,
                )
            )
            continue
        except ProviderResponseError:
            errors.append(
                SafeError(
                    code=ErrorCode.PROVIDER_RESPONSE_INVALID,
                    message="공급자 응답을 신뢰할 수 없어 해당 자료를 제외했습니다.",
                    source_id=source.source_ref.source_id,
                )
            )
            continue
        claims.extend(
            _validated_claims(candidates, evidence_for_source, available, artifacts_by_id, ports)
        )
        requirements.extend(
            _validated_requirements(
                req_candidates, evidence_for_source, available, artifacts_by_id, ports
            )
        )

    claims = list(deduplicate_claims(tuple(claims)))
    presence = (
        RequirementPresenceState.FOUND
        if requirements
        else (
            RequirementPresenceState.NONE_IN_EXAMINED_SCOPE
            if assessment_possible
            else RequirementPresenceState.NOT_ASSESSED
        )
    )
    status = _process_status(claims, requirements, limitations, errors)
    bundle = KnowledgeBundle(
        evidences=tuple(all_evidence),
        claims=tuple(claims),
        requirements=tuple(requirements),
        requirement_presence=presence,
        source_reviews=tuple(reviews),
        limitations=tuple(_unique(limitations)),
        mode=request.context.mode,
        purpose=request.context.purpose,
    )
    return StructureResponse(bundle=bundle, status=status, errors=tuple(errors))


def _evidence_for_source(source: SourceInput) -> list[Evidence]:
    result: list[Evidence] = []
    for artifact in source.artifacts:
        if artifact.text is not None:
            evidence_id = artifact.upstream_evidence_id or f"evidence-{artifact.artifact_id}"
            result.append(
                make_evidence(
                    evidence_id=evidence_id,
                    artifact=artifact,
                    start_offset=0,
                    end_offset=len(artifact.text),
                )
            )
    return result


def _reference_checks(
    candidate_evidence_ids: tuple[str, ...], available: set[str]
) -> tuple[ValidationCheck, ...]:
    status = (
        CheckStatus.PASS
        if candidate_has_evidence(candidate_evidence_ids, available)
        else CheckStatus.FAIL
    )
    code = (
        "EVIDENCE_REFERENCES_PRESENT"
        if status == CheckStatus.PASS
        else "EVIDENCE_REFERENCE_INVALID"
    )
    return (
        ValidationCheck(
            check_id="candidate-evidence",
            status=status,
            code=code,
            message="후보의 Evidence 참조를 확인했습니다.",
        ),
    )


def _checks_for(
    candidate: ClaimCandidate | RequirementCandidate,
    evidence: list[Evidence],
    available: set[str],
    artifacts_by_id: dict[str, Artifact],
) -> tuple[ValidationCheck, ...]:
    result = list(_reference_checks(candidate.evidence_ids, available))
    indexed = {item.evidence_id: item for item in evidence}
    for evidence_id in candidate.evidence_ids:
        item = indexed.get(evidence_id)
        if item is None:
            continue
        artifact = artifacts_by_id.get(item.artifact_id)
        if artifact is None:
            result.append(
                ValidationCheck(
                    check_id=f"{evidence_id}:artifact",
                    status=CheckStatus.FAIL,
                    code="EVIDENCE_ARTIFACT_MISSING",
                    message="Evidence references an Artifact absent from this SourceVersion.",
                    evidence_id=evidence_id,
                )
            )
            continue
        result.extend(evidence_checks(item, artifact))
    return tuple(result)


def _validated_claims(
    candidates: tuple[ClaimCandidate, ...],
    evidence: list[Evidence],
    available: set[str],
    artifacts_by_id: dict[str, Artifact],
    ports: W3Ports,
) -> list[Claim]:
    result: list[Claim] = []
    for candidate in candidates:
        checks = (
            _checks_for(candidate, evidence, available, artifacts_by_id)
            + ports.semantic_validation.validate_claim(candidate)
            + ports.additional_verification.verify_claim(candidate)
        )
        status = verification_status(tuple(item.status for item in checks))
        result.append(
            Claim(
                **candidate.model_dump(),
                verification_status=status,
                usage_status=usage_status(status),
                checks=checks,
            )
        )
    return result


def _validated_requirements(
    candidates: tuple[RequirementCandidate, ...],
    evidence: list[Evidence],
    available: set[str],
    artifacts_by_id: dict[str, Artifact],
    ports: W3Ports,
) -> list[Requirement]:
    result: list[Requirement] = []
    for candidate in candidates:
        structural: tuple[ValidationCheck, ...] = ()
        try:
            validate_requirement_candidate(candidate)
        except ConditionTreeError as exc:
            structural = (
                ValidationCheck(
                    check_id="condition-tree",
                    status=CheckStatus.FAIL,
                    code="CONDITION_TREE_INVALID",
                    message=str(exc),
                ),
            )
        checks = (
            _checks_for(candidate, evidence, available, artifacts_by_id)
            + structural
            + ports.semantic_validation.validate_requirement(candidate)
            + ports.additional_verification.verify_requirement(candidate)
        )
        status = verification_status(tuple(item.status for item in checks))
        result.append(
            Requirement(
                **candidate.model_dump(),
                verification_status=status,
                usage_status=usage_status(status),
                checks=checks,
            )
        )
    return result


def _blocked(request: StructureRequest, error: SafeError) -> StructureResponse:
    bundle = KnowledgeBundle(
        requirement_presence=RequirementPresenceState.NOT_ASSESSED,
        source_reviews=(),
        limitations=(Limitation(code=error.code.value, impact=error.message),),
        mode=request.context.mode,
        purpose=request.context.purpose,
    )
    return StructureResponse(bundle=bundle, status=ProcessingStatus.PAUSED, errors=(error,))


def _process_status(
    claims: list[Claim],
    requirements: list[Requirement],
    limitations: list[Limitation],
    errors: list[SafeError],
) -> ProcessingStatus:
    if errors:
        return ProcessingStatus.LIMITED
    if limitations or any(item.usage_status.value != "USABLE" for item in [*claims, *requirements]):
        return ProcessingStatus.LIMITED
    return ProcessingStatus.COMPLETED


def _unique(limitations: list[Limitation]) -> list[Limitation]:
    unique: dict[tuple[str, str | None], Limitation] = {}
    for item in limitations:
        unique[(item.code, item.artifact_id)] = item
    return list(unique.values())
