"""기존 수집 계약 샘플을 진단용 W3 입력으로 바꾼다.

요약은 Evidence로 승격하지 않으며 네트워크·파일 다운로드를 수행하지 않는다.
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..models import Artifact, Limitation, SourceInput, SourceRef


def load_collection_sample(path: Path) -> tuple[SourceInput, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = _records(payload)
    return tuple(_to_source(record, index) for index, record in enumerate(records))


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ValueError("legacy 수집 샘플 최상위는 object 또는 array여야 합니다.")
    for key in ("sources", "source_envelopes", "items", "documents"):
        if isinstance(payload.get(key), list):
            return [item for item in payload[key] if isinstance(item, dict)]
    return [payload]


def _to_source(record: dict[str, Any], index: int) -> SourceInput:
    source_id = _id(_find_text(record, "source_id", "id"), f"legacy-source-{index}")
    version_value = _find_text(record, "source_version_id", "version_id", "version")
    source_ref = SourceRef(
        source_id=source_id,
        source_version_id=_id(version_value, f"{source_id}-version-unresolved"),
        source_kind="legacy_collection_sample",
    )
    artifact = Artifact(
        artifact_id=f"{source_id}-summary",
        source_ref=source_ref,
        text=None,
        collection_status=str(record.get("collection_status", "UPSTREAM_REPORTED")),
        parsing_status="SUMMARY_ONLY",
        access_status=str(record.get("access_status", "UNKNOWN")),
        retention_status=str(record.get("retention_status", "UNKNOWN")),
    )
    limits = [
        Limitation(
            code="SUMMARY_NOT_EVIDENCE",
            impact="요약은 원문 Evidence나 검증된 Claim으로 사용할 수 없습니다.",
            source_ref=source_ref,
            artifact_id=artifact.artifact_id,
        )
    ]
    if version_value is None:
        limits.append(
            Limitation(
                code="SOURCE_VERSION_UNRESOLVED",
                impact="원본 SourceVersion을 확인해야 합니다.",
                source_ref=source_ref,
            )
        )
    return SourceInput(source_ref=source_ref, artifacts=(artifact,), limitations=tuple(limits))


def _find_text(value: Any, *keys: str) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        for candidate in value.values():
            found = _find_text(candidate, *keys)
            if found:
                return found
    if isinstance(value, list):
        for candidate in value:
            found = _find_text(candidate, *keys)
            if found:
                return found
    return None


def _id(value: str | None, fallback: str) -> str:
    raw = value or fallback
    normalized = re.sub(r"[^A-Za-z0-9._:-]", "-", raw).strip("-._:")
    if not normalized or not normalized[0].isalpha():
        normalized = f"id-{normalized or sha256(raw.encode()).hexdigest()[:12]}"
    return normalized[:128]
