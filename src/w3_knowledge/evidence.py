"""원문 발췌와 무결성 상태를 평가한다."""

from __future__ import annotations

from hashlib import sha256

from .models import Artifact, CheckStatus, Evidence, IntegrityAssertion, SourceRef, ValidationCheck


def digest_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def observed_integrity(text: str, scope: str = "artifact") -> IntegrityAssertion:
    return IntegrityAssertion(digest=digest_text(text), scope=scope)  # type: ignore[arg-type]


def make_evidence(
    *, evidence_id: str, artifact: Artifact, start_offset: int, end_offset: int
) -> Evidence:
    excerpt = None if artifact.text is None else artifact.text[start_offset:end_offset]
    expected_integrity = artifact.expected_integrity
    if expected_integrity is not None and expected_integrity.reported_by == "UPSTREAM_REPORTED":
        expected_integrity = None
    integrity_scope = "artifact" if expected_integrity is None else expected_integrity.scope
    integrity_text = artifact.text if integrity_scope == "artifact" else excerpt
    return Evidence(
        evidence_id=evidence_id,
        source_ref=artifact.source_ref,
        artifact_id=artifact.artifact_id,
        excerpt=excerpt,
        start_offset=start_offset,
        end_offset=end_offset,
        native_locator=artifact.native_locator,
        expected_integrity=expected_integrity,
        observed_integrity=None
        if integrity_text is None
        else observed_integrity(integrity_text, integrity_scope),
    )


def evidence_checks(evidence: Evidence, artifact: Artifact) -> tuple[ValidationCheck, ...]:
    checks: list[ValidationCheck] = []
    ref: SourceRef = evidence.source_ref
    expected_integrity = artifact.expected_integrity
    upstream_integrity = artifact.upstream_integrity
    if expected_integrity is not None and expected_integrity.reported_by == "UPSTREAM_REPORTED":
        upstream_integrity = (*upstream_integrity, expected_integrity)
        expected_integrity = None
    for _report in upstream_integrity:
        checks.append(
            ValidationCheck(
                check_id=f"{evidence.evidence_id}:upstream-integrity",
                status=CheckStatus.NOT_REQUIRED,
                code="UPSTREAM_INTEGRITY_REPORTED",
                message="수집 측 무결성 보고이며 W3의 현재 재검증 결과가 아닙니다.",
                source_ref=ref,
                evidence_id=evidence.evidence_id,
                reported_by="UPSTREAM_REPORTED",
            )
        )
    if evidence.source_ref != artifact.source_ref:
        checks.append(
            _check(
                "SOURCE_VERSION_MATCH",
                CheckStatus.FAIL,
                "SOURCE_VERSION_MISMATCH",
                "근거와 자료의 SourceVersion이 다릅니다.",
                ref,
                evidence.evidence_id,
            )
        )
        return tuple(checks)
    if artifact.text is None:
        checks.append(
            _check(
                "RAW_TEXT",
                CheckStatus.FAIL,
                "RAW_TEXT_MISSING",
                "원문 텍스트가 없어 발췌를 검증할 수 없습니다.",
                ref,
                evidence.evidence_id,
            )
        )
    elif evidence.start_offset is None or evidence.end_offset is None or evidence.excerpt is None:
        checks.append(
            _check(
                "EXCERPT_RANGE",
                CheckStatus.FAIL,
                "EXCERPT_RANGE_MISSING",
                "발췌 offset 또는 발췌문이 없습니다.",
                ref,
                evidence.evidence_id,
            )
        )
    elif artifact.text[evidence.start_offset : evidence.end_offset] != evidence.excerpt:
        checks.append(
            _check(
                "EXCERPT_RANGE",
                CheckStatus.FAIL,
                "EXCERPT_MISMATCH",
                "발췌문이 원문 offset과 일치하지 않습니다.",
                ref,
                evidence.evidence_id,
            )
        )
    else:
        checks.append(
            _check(
                "EXCERPT_RANGE",
                CheckStatus.PASS,
                "EXCERPT_MATCH",
                "발췌문과 원문 offset이 일치합니다.",
                ref,
                evidence.evidence_id,
            )
        )

    if expected_integrity is None:
        checks.append(
            _check(
                "EXPECTED_DIGEST",
                CheckStatus.PENDING,
                "EXPECTED_DIGEST_MISSING",
                "기대 digest가 없어 무결성을 확정할 수 없습니다.",
                ref,
                evidence.evidence_id,
            )
        )
    elif artifact.text is None:
        checks.append(
            _check(
                "EXPECTED_DIGEST",
                CheckStatus.PENDING,
                "OBSERVED_DIGEST_UNAVAILABLE",
                "원문이 없어 관찰 digest를 계산할 수 없습니다.",
                ref,
                evidence.evidence_id,
            )
        )
    elif expected_integrity.scope == "excerpt" and evidence.excerpt is None:
        checks.append(
            _check(
                "EXPECTED_DIGEST",
                CheckStatus.PENDING,
                "EXCERPT_DIGEST_UNAVAILABLE",
                "발췌문이 없어 발췌 범위의 관찰 digest를 계산할 수 없습니다.",
                ref,
                evidence.evidence_id,
            )
        )
    elif (
        digest_text(artifact.text if expected_integrity.scope == "artifact" else evidence.excerpt)
        != expected_integrity.digest
    ):
        checks.append(
            _check(
                "EXPECTED_DIGEST",
                CheckStatus.FAIL,
                "DIGEST_MISMATCH",
                "관찰 digest가 기대 digest와 다릅니다.",
                ref,
                evidence.evidence_id,
            )
        )
    else:
        checks.append(
            _check(
                "EXPECTED_DIGEST",
                CheckStatus.PASS,
                "DIGEST_MATCH",
                "관찰 digest가 기대 digest와 일치합니다.",
                ref,
                evidence.evidence_id,
            )
        )

    if artifact.native_locator is None:
        checks.append(
            _check(
                "NATIVE_LOCATOR",
                CheckStatus.PENDING,
                "NATIVE_LOCATOR_MISSING",
                "원본 위치를 재현할 locator가 없습니다.",
                ref,
                evidence.evidence_id,
            )
        )
    elif artifact.native_locator.reproducible:
        checks.append(
            _check(
                "NATIVE_LOCATOR",
                CheckStatus.PASS,
                "NATIVE_LOCATOR_REPRODUCIBLE",
                "원본 위치를 재현할 수 있습니다.",
                ref,
                evidence.evidence_id,
            )
        )
    else:
        checks.append(
            _check(
                "NATIVE_LOCATOR",
                CheckStatus.PENDING,
                "NATIVE_LOCATOR_UNCONFIRMED",
                "제공된 locator의 재현성을 확인하지 못했습니다.",
                ref,
                evidence.evidence_id,
            )
        )
    return tuple(checks)


def _check(
    check_id: str,
    status: CheckStatus,
    code: str,
    message: str,
    source_ref: SourceRef,
    evidence_id: str,
) -> ValidationCheck:
    return ValidationCheck(
        check_id=check_id,
        status=status,
        code=code,
        message=message,
        source_ref=source_ref,
        evidence_id=evidence_id,
    )
