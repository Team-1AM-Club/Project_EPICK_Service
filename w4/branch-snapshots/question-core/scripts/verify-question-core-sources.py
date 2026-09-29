"""Record original-source availability and verify the user-provided W2 pin."""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
W2_SHA = "16a7bd2653873a20a563e6d2f54c24c6dc18c373"
W1_SHA = "9cbfa4284b5f74d2492618e71518ffe13d3b7a45"
W1_OTHER = "5b056261591b91f8ed84ded50ee0f5885b92c58d"
W1_PATHS = [
    "specs/004-w1-w4-question-core/w4-adoption.md",
    "specs/004-w1-w4-question-core/contracts/w4-question-core-decision.event.schema.json",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git", default="git")
    parser.add_argument("--service-reference", type=Path, required=True)
    parser.add_argument("--request-file", type=Path, required=True)
    args = parser.parse_args()

    def git(repo, *command):
        return subprocess.check_output(
            [args.git, "-C", str(repo), *command], stderr=subprocess.PIPE
        )

    def blob(repo, sha, path):
        return git(repo, "show", f"{sha}:{path}")

    def pin(path, content):
        target = ROOT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != content:
            raise ValueError("Refusing to replace a different pinned source")
        target.write_bytes(content)
        return {"path": path, "sha256": hashlib.sha256(content).hexdigest()}

    manifest_path = "contracts/w2-private/artifacts.sha256"
    manifest = blob(ROOT, W2_SHA, manifest_path)
    checks = []
    for line in manifest.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
        if not match:
            raise ValueError("Invalid checksum manifest")
        expected, path = match.groups()
        actual = hashlib.sha256(blob(ROOT, W2_SHA, path)).hexdigest()
        if actual != expected:
            raise ValueError("W2 checksum mismatch: " + path)
        checks.append({"path": path, "sha256": actual})
    w2_tree = set(git(ROOT, "ls-tree", "-r", "--name-only", W2_SHA).decode().splitlines())
    availability = []
    for sha in (W1_SHA, W1_OTHER):
        tree = set(
            git(args.service_reference, "ls-tree", "-r", "--name-only", sha).decode().splitlines()
        )
        availability.append(
            {
                "repository": "Project_EPICK_Service",
                "sha": sha,
                "requested_files": {path: path in tree for path in W1_PATHS},
            }
        )
    availability.append(
        {
            "repository": "Project_EPICK_Engine",
            "sha": W2_SHA,
            "requested_files": {path: path in w2_tree for path in W1_PATHS},
        }
    )
    pinned = [
        pin("docs/inputs/W1_W4_Required_Contracts_2026-09-18.md", args.request_file.read_bytes())
    ]
    for filename in ("W2_Implementation_Handoff_2026-09-18.md", "artifacts.sha256"):
        pinned.append(
            pin(
                "samples/question-core-local/upstream/w2-" + filename,
                blob(ROOT, W2_SHA, "contracts/w2-private/" + filename),
            )
        )
    helper = "backend/app/runtime/core_decision_binding.py"
    pinned.append(
        pin(
            "samples/question-core-local/upstream/w1_core_decision_binding.py",
            blob(args.service_reference, W1_SHA, helper),
        )
    )
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "w1_original_status": "NOT_FOUND_IN_CHECKED_COMMITS",
        "w1_004_schema_approved": False,
        "availability": availability,
        "w2_delivery_sha": W2_SHA,
        "w2_checksum_verified_count": len(checks),
        "w2_checksum_results": checks,
        "pinned_sources": pinned,
        "digest_helper": {
            "repository": "Project_EPICK_Service",
            "sha": W1_SHA,
            "path": helper,
            "scope": "EXISTING_GENERIC_HELPER_NOT_W4_004_CONSUMER",
        },
    }
    target = ROOT / "samples/question-core-local/source-review.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"w2_artifacts_verified": len(checks), "w1_original": report["w1_original_status"]}
        )
    )


if __name__ == "__main__":
    main()
