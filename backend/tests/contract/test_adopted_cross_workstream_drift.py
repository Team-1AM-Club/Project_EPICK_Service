"""Cross-workstream byte pins for already adopted private contracts.

W2's sibling ``specs`` reference bundle is intentionally not used here. Service
contracts are the W1/W2 authority; received copies are checked against them.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SERVICE_CONTRACTS = ROOT / "backend" / "contracts"
W2_ROOT = Path(os.environ.get("EPICK_W2_ROOT", ROOT / "w2" / "Project_EPICK_Engine"))
W3_ROOT = Path(os.environ.get("EPICK_W3_ROOT", ROOT / "w3" / "Project_EPICK_Service"))
W4_ROOT = Path(os.environ.get("EPICK_W4_ROOT", ROOT / "w4" / "Project_EPICK_Engine"))

W2_RUNTIME_SCHEMA_PINS = {
    "source-collection.staged-result.schema.json": (
        "96a820dd3c40746aa8e7a2ac6cb34b3ee73efa5f748e3149e68584ecca08d77d"
    ),
    "source-collection.commit-gate-ack.schema.json": (
        "c7470349720e217a090798000f9e2ead34b96bd7c00db33050405c2f151d5d99"
    ),
}
W3_W4_C01_SCHEMA_PINS = {
    "signal.schema.json": "c4f2c50681f94d12f2e079e4ff1b6f93893f25c4b94562f8a066ed87169021f5",
    "status.schema.json": "a4ba653fd9f4bd149117a97b3a4843b2567f02f0be6017634a42ac5f3972641d",
    "knowledge.schema.json": "a221f298f59bd524d80c7873a3d3f731c04ea5d70188ec49f76729ec2c0c6504",
    "delivery-response.schema.json": (
        "65d641de630a4362e8d32f71e4f98dc22a5e99d602e72661db1cd5c572536fb5"
    ),
    "delivery-receipt.schema.json": (
        "76ef0e6ad095b0bc0b0f1ba17917898854c5acd9c3ff03efa2168523cdb80a20"
    ),
}
INTEGRATION_CONTRACT_PINS = {
    "deployment-manifest.schema.json": (
        "148f1c854cc19e5e06d443ef32771f9cd92d4bca9fd85469b42034a8eb0cffe4"
    ),
    "public-collection-api.openapi.yaml": (
        "13107b34c8171c72c39324267b09a7d072a35fb29b38bc2adcb1f0f0e534be94"
    ),
    "w1-w3-analysis-plan-command.schema.json": (
        "136d0fbe9a4f1978f658c7f3fa37f549269431047eb1917bf699c9bcae802145"
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.skipif(not W2_ROOT.is_dir(), reason="independent W2 clone is unavailable")
def test_w2_received_w1_contract_snapshots_match_service_authority() -> None:
    received = W2_ROOT / "tests" / "fixtures" / "w1_private_contract"
    manifest = json.loads((received / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["files"]) >= 30
    for entry in manifest["files"]:
        canonical = ROOT / entry["upstream_path"]
        assert canonical.resolve().is_relative_to(SERVICE_CONTRACTS.resolve())
        assert _sha256(canonical) == entry["sha256"], entry["upstream_path"]
        assert _sha256(received / entry["file"]) == entry["sha256"], entry["file"]


@pytest.mark.skipif(not W2_ROOT.is_dir(), reason="independent W2 clone is unavailable")
def test_w2_owned_ack_and_staged_result_match_service_adopted_schemas() -> None:
    for name, expected in W2_RUNTIME_SCHEMA_PINS.items():
        assert _sha256(SERVICE_CONTRACTS / "w2" / "v1" / name) == expected
        assert _sha256(W2_ROOT / "contracts" / "w2-private" / name) == expected


@pytest.mark.skipif(not W3_ROOT.is_dir(), reason="independent W3 clone is unavailable")
def test_w3_received_w2_source_event_copy_matches_service_authority() -> None:
    manifest = json.loads(
        (
            W3_ROOT / "contracts" / "c01" / "v0.2-candidate" / "received-manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert len(manifest["sha256"]) == 7
    for path, expected in manifest["sha256"].items():
        canonical = (
            SERVICE_CONTRACTS / "w2" / "v1" / "source-event-payload.schema.json"
            if path.endswith("source-event-payload.schema.json")
            else SERVICE_CONTRACTS / "fixtures" / "v1" / "w2" / Path(path).name
        )
        assert _sha256(canonical) == expected, path
        assert _sha256(W3_ROOT / path) == expected, path


@pytest.mark.skipif(
    not W3_ROOT.is_dir() or not W4_ROOT.is_dir(),
    reason="independent W3/W4 clones are unavailable",
)
def test_w4_received_w3_c01_schemas_match_pinned_producer_bytes() -> None:
    for name, expected in W3_W4_C01_SCHEMA_PINS.items():
        assert _raw_sha256(W3_ROOT / "contracts" / "c01" / "v0.2-candidate" / name) == expected
        assert _raw_sha256(W4_ROOT / "epick_w4" / "c01_schemas" / name) == expected


@pytest.mark.skipif(
    not W2_ROOT.is_dir() or not W3_ROOT.is_dir(),
    reason="independent W2/W3 clones are unavailable",
)
@pytest.mark.parametrize(("consumer", "consumer_root"), (("w2", W2_ROOT), ("w3", W3_ROOT)))
def test_shared_integration_consumer_manifests_match_w1_authority(
    consumer: str, consumer_root: Path
) -> None:
    manifest = json.loads(
        (consumer_root / "contracts" / "integration" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["schema_version"] == "epick.integration-consumer-pins/1.0"
    assert manifest["consumer"] == consumer
    assert manifest["authority_root"] == "backend/contracts/integration/v1"
    assert manifest["contracts"] == INTEGRATION_CONTRACT_PINS
    for name, expected in INTEGRATION_CONTRACT_PINS.items():
        assert _sha256(SERVICE_CONTRACTS / "integration" / "v1" / name) == expected
