from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verify_w3_runtime_provenance import (
    W3ProvenanceError,
    verify_w3_runtime_provenance,
)

REPO_ROOT = Path(__file__).parents[3]
W3_ROOT = REPO_ROOT / "w3"
EXPECTED_SHA = "c7e6788168c048941bdabe7ed8cb01007edeecec"


def test_received_w3_package_matches_pinned_receipt_and_handoff_hash() -> None:
    result = verify_w3_runtime_provenance(w3_root=W3_ROOT, expected_sha=EXPECTED_SHA)

    assert result == {
        "status": "ok",
        "contract": "w3.private.core-decision/0.1-candidate",
        "source_sha": EXPECTED_SHA,
        "canonical_path": "docs/w3-core-runtime-handoff-2026-09-18.md",
        "canonical_sha256": "917ff83a93e29740f7e92452be61e3cefb7ff964531e1ed2847971814671ea24",
        "network_calls": 0,
    }


def test_provenance_rejects_a_different_expected_sha() -> None:
    with pytest.raises(W3ProvenanceError, match="source SHA"):
        verify_w3_runtime_provenance(w3_root=W3_ROOT, expected_sha="0" * 40)


def test_provenance_rejects_modified_handoff_bytes(tmp_path: Path) -> None:
    receipt = json.loads((W3_ROOT / "HANDOFF_RECEIPT.json").read_text(encoding="utf-8"))
    (tmp_path / "HANDOFF_RECEIPT.json").write_text(json.dumps(receipt), encoding="utf-8")
    canonical = tmp_path / receipt["canonical_path"]
    canonical.parent.mkdir(parents=True)
    canonical.write_text("modified", encoding="utf-8")

    with pytest.raises(W3ProvenanceError, match="canonical SHA-256"):
        verify_w3_runtime_provenance(w3_root=tmp_path, expected_sha=EXPECTED_SHA)
