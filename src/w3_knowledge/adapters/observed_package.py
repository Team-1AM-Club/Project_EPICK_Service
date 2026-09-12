"""PM observed JSON 두 파일을 읽기 전용 진단 입력으로 매핑한다."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..models import Artifact, IntegrityAssertion, Limitation, NativeLocator, SourceInput, SourceRef


def load_observed_package(package_root: Path) -> tuple[SourceInput, ...]:
    root = package_root.resolve()
    observed = root / "observed"
    envelopes = _read_json(observed / "source-envelopes.json")
    attachments = _read_json(observed / "attachment-extraction.json")
    sources = [
        _envelope_source(item, index) for index, item in enumerate(_list(envelopes, "sources"))
    ]
    sources.extend(
        _attachment_source(item, index)
        for index, item in enumerate(_list(attachments, "attachments"))
    )
    return tuple(sources)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"필수 observed 입력이 없습니다: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 최상위는 object여야 합니다.")
    return value


def _list(value: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records = value.get(key)
    if not isinstance(records, list):
        raise ValueError(f"{key} array가 없습니다.")
    return [item for item in records if isinstance(item, dict)]


def _envelope_source(item: dict[str, Any], index: int) -> SourceInput:
    nested_source = item.get("source") if isinstance(item.get("source"), dict) else {}
    nested_version = (
        item.get("source_version") if isinstance(item.get("source_version"), dict) else {}
    )
    source_id = _id(
        _text(nested_source, "source_id", "id") or _text(item, "source_id"), f"pm-envelope-{index}"
    )
    version = _id(
        _text(nested_version, "source_version_id", "id", "version_id")
        or _text(item, "source_version_id"),
        f"{source_id}-version-unresolved",
    )
    ref = SourceRef(
        source_id=source_id,
        source_version_id=version,
        source_kind="pm_observed_envelope",
        parent_source_id=_text(nested_source, "parent_source_id"),
    )
    artifacts = _span_artifacts(
        item.get("evidence_spans"),
        ref=ref,
        prefix=f"{source_id}-envelope",
        pointer=f"observed/source-envelopes.json#/sources/{index}/evidence_spans",
        collection_status=_status(item, "acquisition_status"),
        parsing_status=_status(nested_version, "extraction_status"),
        access_status=_status(nested_source, "access_status"),
    )
    if not artifacts:
        artifacts = (
            _metadata_artifact(
                f"{source_id}-envelope",
                ref,
                f"observed/source-envelopes.json#/sources/{index}",
                _status(item, "acquisition_status"),
            ),
        )
    limitations = tuple(
        Limitation(
            code="UPSTREAM_EXCERPT_UNVERIFIED",
            impact="제공된 발췌는 원본 재현과 W3 digest 검증 전까지 검증된 원문이 아닙니다.",
            source_ref=ref,
            artifact_id=artifact.artifact_id,
        )
        for artifact in artifacts
    )
    return SourceInput(source_ref=ref, artifacts=artifacts, limitations=limitations)


def _attachment_source(item: dict[str, Any], index: int) -> SourceInput:
    source_id = _id(_text(item, "source_id", "attachment_id"), f"pm-attachment-{index}")
    version = _id(_text(item, "source_version_id"), f"{source_id}-version-unresolved")
    parent = _text(item, "parent_source_id")
    ref = SourceRef(
        source_id=source_id,
        source_version_id=version,
        source_kind="pm_observed_attachment",
        parent_source_id=parent,
    )
    artifacts = _span_artifacts(
        item.get("evidence_spans"),
        ref=ref,
        prefix=_id(_text(item, "attachment_id"), f"{source_id}-artifact"),
        pointer=f"observed/attachment-extraction.json#/attachments/{index}/evidence_spans",
        collection_status=_status(item, "download"),
        parsing_status=_status(item, "extraction"),
        access_status=_status(item, "rights"),
        upstream_integrity=_upstream_integrity(item),
        required_for_scope=True,
    )
    if not artifacts:
        artifacts = (
            _metadata_artifact(
                _id(_text(item, "attachment_id"), f"{source_id}-artifact"),
                ref,
                f"observed/attachment-extraction.json#/attachments/{index}",
                _status(item, "download"),
            ),
        )
    limitations: list[Limitation] = []
    for artifact in artifacts:
        if artifact.text is None:
            limitations.append(
                Limitation(
                    code="RAW_TEXT_MISSING",
                    impact="첨부에서 재현 가능한 원문 발췌를 확인하지 못했습니다.",
                    source_ref=ref,
                    artifact_id=artifact.artifact_id,
                )
            )
        if not artifact.upstream_integrity:
            limitations.append(
                Limitation(
                    code="EXPECTED_DIGEST_MISSING",
                    impact="upstream 보고와 W3 무결성 검증은 구분됩니다.",
                    source_ref=ref,
                    artifact_id=artifact.artifact_id,
                )
            )
    return SourceInput(source_ref=ref, artifacts=artifacts, limitations=tuple(limitations))


def _extract_excerpt(item: dict[str, Any]) -> str | None:
    spans = item.get("evidence_spans")
    if not isinstance(spans, list):
        return None
    text_parts: list[str] = []
    for span in spans:
        if isinstance(span, dict):
            for key in ("text_excerpt", "excerpt", "text", "content"):
                value = span.get(key)
                if isinstance(value, str) and value.strip():
                    text_parts.append(value)
                    break
    return "\n".join(text_parts) or None


def _span_artifacts(
    spans: Any,
    *,
    ref: SourceRef,
    prefix: str,
    pointer: str,
    collection_status: str,
    parsing_status: str,
    access_status: str,
    upstream_integrity: tuple[IntegrityAssertion, ...] = (),
    required_for_scope: bool = False,
) -> tuple[Artifact, ...]:
    if not isinstance(spans, list):
        return ()
    result: list[Artifact] = []
    for offset, span in enumerate(spans):
        if not isinstance(span, dict):
            continue
        text = _extract_excerpt({"evidence_spans": [span]})
        evidence_id = _id(_text(span, "evidence_id"), f"{prefix}-evidence-{offset}")
        result.append(
            Artifact(
                artifact_id=f"{prefix}-{offset}",
                source_ref=ref,
                upstream_evidence_id=evidence_id,
                text=text,
                native_locator=NativeLocator(
                    locator_type="external_reference",
                    value=f"{pointer}/{offset}",
                    reproducible=False,
                ),
                upstream_integrity=upstream_integrity,
                collection_status=collection_status,
                parsing_status=parsing_status,
                access_status=access_status,
                retention_status="UPSTREAM_REPORTED",
                required_for_scope=required_for_scope,
            )
        )
    return tuple(result)


def _metadata_artifact(
    artifact_id: str, ref: SourceRef, pointer: str, collection_status: str
) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        source_ref=ref,
        native_locator=NativeLocator(locator_type="json_pointer", value=pointer, reproducible=True),
        collection_status=collection_status,
        parsing_status="METADATA_ONLY",
        access_status="UNKNOWN",
        retention_status="UPSTREAM_REPORTED",
        required_for_scope=True,
    )


def _upstream_integrity(value: Any) -> tuple[IntegrityAssertion, ...]:
    found = _find_digest(value)
    return (
        ()
        if found is None
        else (IntegrityAssertion(digest=found, reported_by="UPSTREAM_REPORTED"),)
    )


def _find_digest(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("sha256", "digest"):
            candidate = value.get(key)
            if isinstance(candidate, str) and re.fullmatch(r"[a-f0-9]{64}", candidate):
                return candidate
        for candidate in value.values():
            found = _find_digest(candidate)
            if found:
                return found
    if isinstance(value, list):
        for candidate in value:
            found = _find_digest(candidate)
            if found:
                return found
    return None


def _text(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def _status(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if isinstance(item, str):
        return item.upper()
    if isinstance(item, dict):
        for status_key in ("status", "state", "result"):
            result = item.get(status_key)
            if isinstance(result, str):
                return result.upper()
    return "UNKNOWN"


def _id(value: str | None, fallback: str) -> str:
    raw = value or fallback
    normalized = re.sub(r"[^A-Za-z0-9._:-]", "-", raw).strip("-._:")
    if not normalized or not normalized[0].isalpha():
        normalized = f"id-{normalized or sha256(raw.encode()).hexdigest()[:12]}"
    return normalized[:128]
