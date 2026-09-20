from __future__ import annotations

import hashlib
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
EXPECTED_HANDOFF_HASH = "917ff83a93e29740f7e92452be61e3cefb7ff964531e1ed2847971814671ea24"


@pytest.mark.skipif(
    not (W3_ROOT / "HANDOFF_RECEIPT.json").is_file(),
    reason="W3 checkout is not available in this CI job",
)
def test_received_w3_package_matches_pinned_receipt_and_handoff_hash() -> None:
    result = verify_w3_runtime_provenance(w3_root=W3_ROOT, expected_sha=EXPECTED_SHA)

    assert result == {
        "status": "ok",
        "contract": "w3.private.core-decision/0.1-candidate",
        "source_sha": EXPECTED_SHA,
        "canonical_path": "docs/w3-core-runtime-handoff-2026-09-18.md",
        "canonical_sha256": EXPECTED_HANDOFF_HASH,
        "network_calls": 0,
    }


def test_synthetic_receipt_exercises_success_without_external_checkout(tmp_path: Path) -> None:
    expected_digest = _write_fixture(tmp_path)

    result = verify_w3_runtime_provenance(w3_root=tmp_path, expected_sha=EXPECTED_SHA)

    assert result["status"] == "ok"
    assert result["source_sha"] == EXPECTED_SHA
    assert result["canonical_sha256"] == expected_digest
    assert result["network_calls"] == 0


def test_provenance_rejects_a_different_expected_sha(tmp_path: Path) -> None:
    _write_fixture(tmp_path)

    with pytest.raises(W3ProvenanceError, match="source SHA"):
        verify_w3_runtime_provenance(w3_root=tmp_path, expected_sha="0" * 40)


def test_provenance_rejects_modified_handoff_bytes(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    canonical = tmp_path / "docs" / "w3-core-runtime-handoff.md"
    canonical.write_text("modified", encoding="utf-8")

    with pytest.raises(W3ProvenanceError, match="canonical SHA-256"):
        verify_w3_runtime_provenance(w3_root=tmp_path, expected_sha=EXPECTED_SHA)


def _write_fixture(root: Path) -> str:
    canonical_path = "docs/w3-core-runtime-handoff.md"
    canonical = root / canonical_path
    canonical.parent.mkdir(parents=True)
    canonical.write_text("synthetic handoff\n", encoding="utf-8")
    digest = hashlib.sha256(canonical.read_bytes()).hexdigest()
    receipt = {
        "contract": "w3.private.core-decision/0.1-candidate",
        "full_commit": EXPECTED_SHA,
        "remote_head": EXPECTED_SHA,
        "pushed": True,
        "remote_fetch_verified": True,
        "canonical_path": canonical_path,
        "canonical_sha256": digest,
    }
    (root / "HANDOFF_RECEIPT.json").write_text(json.dumps(receipt), encoding="utf-8")
    return digest
