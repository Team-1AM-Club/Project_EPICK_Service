"""수집 성공과 구조화 가능성을 분리해 입력을 검토한다."""

from __future__ import annotations

from .models import (
    Artifact,
    CheckStatus,
    Limitation,
    ProcessingStatus,
    SourceInput,
    SourceReview,
    ValidationCheck,
)


def review_source(source: SourceInput) -> SourceReview:
    checks = list(source.envelope_checks)
    limitations = list(source.limitations)
    for artifact in source.artifacts:
        checks.extend(_artifact_checks(artifact))
        if artifact.text is None:
            limitations.append(
                Limitation(
                    code="RAW_TEXT_MISSING",
                    impact="원문 기반 Claim·Requirement 구조화를 제한합니다.",
                    source_ref=source.source_ref,
                    artifact_id=artifact.artifact_id,
                )
            )
        if artifact.required_for_scope and artifact.parsing_status not in {"PARSED", "AVAILABLE"}:
            limitations.append(
                Limitation(
                    code="REQUIRED_ARTIFACT_UNPARSED",
                    impact="필수 첨부의 미파싱은 요구사항 부재가 아니라 미확인입니다.",
                    source_ref=source.source_ref,
                    artifact_id=artifact.artifact_id,
                )
            )
    statuses = {check.status for check in checks}
    if CheckStatus.FAIL in statuses:
        process_status = ProcessingStatus.LIMITED
    elif limitations or CheckStatus.PENDING in statuses:
        process_status = ProcessingStatus.LIMITED
    else:
        process_status = ProcessingStatus.COMPLETED
    return SourceReview(
        source_ref=source.source_ref,
        checks=tuple(checks),
        limitations=tuple(_unique_limitations(limitations)),
        process_status=process_status,
    )


def _artifact_checks(artifact: Artifact) -> tuple[ValidationCheck, ...]:
    source = artifact.source_ref
    values = {
        "COLLECTION": artifact.collection_status,
        "PARSING": artifact.parsing_status,
        "ACCESS": artifact.access_status,
        "RETENTION": artifact.retention_status,
    }
    checks: list[ValidationCheck] = []
    for name, value in values.items():
        status = (
            CheckStatus.PASS
            if value in {"SUCCESS", "PARSED", "AVAILABLE", "RETAINED"}
            else CheckStatus.PENDING
        )
        code = f"{name}_{value}"
        checks.append(
            ValidationCheck(
                check_id=f"{artifact.artifact_id}:{name}",
                status=status,
                code=code,
                message=f"{name} 상태: {value}",
                source_ref=source,
            )
        )
    return tuple(checks)


def _unique_limitations(limitations: list[Limitation]) -> list[Limitation]:
    found: set[tuple[str, str | None]] = set()
    output: list[Limitation] = []
    for limitation in limitations:
        key = (limitation.code, limitation.artifact_id)
        if key not in found:
            found.add(key)
            output.append(limitation)
    return output
