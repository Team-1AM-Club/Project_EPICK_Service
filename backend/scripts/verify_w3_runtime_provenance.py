"""Verify the received W3 runtime package without network access or mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_W3_SHA = "c7e6788168c048941bdabe7ed8cb01007edeecec"


class W3ProvenanceError(ValueError):
    """Safe provenance mismatch that does not include private runtime values."""


def verify_w3_runtime_provenance(
    *, w3_root: Path, expected_sha: str = EXPECTED_W3_SHA
) -> dict[str, Any]:
    root = w3_root.resolve()
    receipt_path = root / "HANDOFF_RECEIPT.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise W3ProvenanceError("W3 handoff receipt is unreadable") from error
    if not isinstance(receipt, dict):
        raise W3ProvenanceError("W3 handoff receipt must be an object")
    if receipt.get("full_commit") != expected_sha or receipt.get("remote_head") != expected_sha:
        raise W3ProvenanceError("W3 source SHA does not match the pinned revision")
    if receipt.get("pushed") is not True or receipt.get("remote_fetch_verified") is not True:
        raise W3ProvenanceError("W3 receipt does not prove the pinned remote revision")

    canonical_value = receipt.get("canonical_path")
    expected_digest = receipt.get("canonical_sha256")
    if not isinstance(canonical_value, str) or not isinstance(expected_digest, str):
        raise W3ProvenanceError("W3 canonical handoff metadata is incomplete")
    canonical = (root / canonical_value).resolve()
    if root not in canonical.parents or not canonical.is_file():
        raise W3ProvenanceError("W3 canonical handoff path is invalid")
    actual_digest = hashlib.sha256(canonical.read_bytes()).hexdigest()
    if actual_digest != expected_digest:
        raise W3ProvenanceError("W3 canonical SHA-256 does not match the receipt")

    contract = receipt.get("contract")
    if contract != "w3.private.core-decision/0.1-candidate":
        raise W3ProvenanceError("W3 runtime contract does not match the adopted version")
    return {
        "status": "ok",
        "contract": contract,
        "source_sha": expected_sha,
        "canonical_path": canonical_value,
        "canonical_sha256": actual_digest,
        "network_calls": 0,
    }


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("--w3-root", type=Path, default=repo_root / "w3")
    parser.add_argument("--expected-sha", default=EXPECTED_W3_SHA)
    args = parser.parse_args()
    result = verify_w3_runtime_provenance(
        w3_root=args.w3_root,
        expected_sha=args.expected_sha,
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except W3ProvenanceError as error:
        raise SystemExit(str(error)) from error
