from __future__ import annotations

import pytest

from app.runtime.w1_w3_evidence import (
    EvidenceRedactionError,
    build_count_snapshot,
    sanitize_public_evidence,
)


def test_allowlisted_count_evidence_is_preserved() -> None:
    assert sanitize_public_evidence(
        {
            "status": "ok",
            "run_id": "synthetic-run-01",
            "counts": {"receipts": 2, "decisions": 2, "commands": 0},
        },
        allowed_fields={"status", "run_id", "counts", "receipts", "decisions", "commands"},
    ) == {
        "status": "ok",
        "run_id": "synthetic-run-01",
        "counts": {"receipts": 2, "decisions": 2, "commands": 0},
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"owner_id": "00000000-0000-0000-0000-000000000001"},
        {"queue_url": "https://sqs.ap-northeast-2.amazonaws.com/123/private"},
        {"token": "Bearer private-token"},
        {"database_url": "postgresql://user:password@example/private"},
        {"role_arn": "arn:aws:iam::123:role/private"},
        {"analysis_plan": {"required_sources": ["private"]}},
        {"source_body": "private source"},
    ],
)
def test_forbidden_fields_are_rejected_even_when_a_caller_allowlists_them(
    payload: dict[str, object],
) -> None:
    with pytest.raises(EvidenceRedactionError):
        sanitize_public_evidence(payload, allowed_fields=set(payload))


@pytest.mark.parametrize(
    "value",
    [
        "AKIAABCDEFGHIJKLMNOP",
        "https://sqs.ap-northeast-2.amazonaws.com/123/private",
        "postgresql+psycopg://user:password@example/private",
        "arn:aws:iam::123:role/private",
        "Bearer private-token",
    ],
)
def test_sensitive_string_shapes_are_rejected(value: str) -> None:
    with pytest.raises(EvidenceRedactionError):
        sanitize_public_evidence({"status": value}, allowed_fields={"status"})


def test_count_snapshot_accepts_only_non_negative_plain_integers() -> None:
    assert build_count_snapshot(receipts=2, commands=0) == {"commands": 0, "receipts": 2}
    with pytest.raises(EvidenceRedactionError, match="non-negative integer"):
        build_count_snapshot(receipts=-1)
    with pytest.raises(EvidenceRedactionError, match="non-negative integer"):
        build_count_snapshot(receipts=True)
