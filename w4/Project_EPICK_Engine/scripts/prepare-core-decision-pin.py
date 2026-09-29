"""Copy exact W1 contract bytes from the reviewed Git object, not a working tree."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PIN = "afec08a9602132e5e433b523b0b6804850440524"
FILES = {
    "backend/contracts/w1/v1/private-message-envelope.schema.json": "epick_w4/core_decision_schemas/envelope.schema.json",
    "backend/contracts/w1/v1/core-source-decision.schema.json": "epick_w4/core_decision_schemas/payload.schema.json",
    "backend/contracts/w1/v1/private-delivery-receipt.schema.json": "epick_w4/core_decision_schemas/receipt.schema.json",
    "backend/contracts/w2/v1/source-collection.command.schema.json": "samples/core-decision/upstream/w2-command.schema.json",
    "backend/app/runtime/core_decision_binding.py": "samples/core-decision/upstream/core_decision_binding.py",
    "backend/contracts/fixtures/v1/w1/private-w2-command-dispatch.json": "samples/core-decision/upstream/w2-dispatch.json",
    "backend/contracts/fixtures/v1/w1/private-core-source-decision-question.json": "samples/core-decision/upstream/question-decision.json",
    "backend/contracts/w1/v1/README.md": "samples/core-decision/upstream/w1-contract-readme.md",
}


def main():
    rows = []
    for source, name in FILES.items():
        data = subprocess.check_output([r"C:\Program Files\Git\cmd\git.exe", "-C",
            str(ROOT / "output/service-reference-20260917"), "show", PIN + ":" + source])
        target = ROOT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != data:
            raise ValueError("Refusing to replace a differing pin")
        target.write_bytes(data)
        rows.append({"upstream_path": source, "local_path": name, "sha256": hashlib.sha256(data).hexdigest()})
    value = {"repository": "https://github.com/Team-1AM-Club/Project_EPICK_Service.git", "commit": PIN,
             "status": "W1_PINNED_W4_PRODUCER_PROPOSAL_NOT_ADOPTED", "files": rows}
    (ROOT / "epick_w4/core_decision_schemas/baseline.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Pinned {len(rows)} exact-byte W1 artifacts at {PIN}")


if __name__ == "__main__":
    main()
