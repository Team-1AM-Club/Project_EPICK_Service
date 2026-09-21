"""Verify the independent W3 Git clone without network access or mutation."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

EXPECTED_IMPLEMENTATION_SHA = "0c4f01f9537a3129c976fae5e63111a7982c5da6"
EXPECTED_RECEIPT_HEAD_SHA = "402f7a63bf8f8d601cc6ada1ce685280f47320ce"
EXPECTED_RUNTIME_SHA = "402f7a63bf8f8d601cc6ada1ce685280f47320ce"
EXPECTED_REMOTE = "https://github.com/Team-1AM-Club/Project_EPICK_Service.git"
EXPECTED_CONTRACT = "w3.private.core-decision/0.1-candidate"
EXPECTED_POLICY_REVISION = "w3.retention/1.1"
READINESS_PATH = Path("contracts/core-runtime/readiness.json")
ARCHIVE_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "src",
    "tests",
    "specs/001-source-knowledge-validation/spec.md",
)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class W3ProvenanceError(ValueError):
    """Safe provenance mismatch that does not include private runtime values."""


def verify_w3_runtime_provenance(
    *,
    w3_root: Path,
    expected_implementation_sha: str = EXPECTED_IMPLEMENTATION_SHA,
    expected_receipt_head_sha: str = EXPECTED_RECEIPT_HEAD_SHA,
    expected_runtime_sha: str | None = None,
    expected_remote: str = EXPECTED_REMOTE,
) -> dict[str, Any]:
    root = w3_root.resolve()
    _validate_expected_sha(expected_implementation_sha, "implementation")
    _validate_expected_sha(expected_receipt_head_sha, "receipt HEAD")
    runtime_sha = expected_runtime_sha or expected_receipt_head_sha
    _validate_expected_sha(runtime_sha, "runtime")
    if not root.is_dir():
        raise W3ProvenanceError("W3 clone directory is unavailable")
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        raise W3ProvenanceError("W3 source is not an independent Git worktree")

    remote = _git(root, "remote", "get-url", "origin")
    if remote != expected_remote:
        raise W3ProvenanceError("W3 origin remote does not match the approved remote")
    if _git(root, "cat-file", "-t", expected_implementation_sha) != "commit":
        raise W3ProvenanceError("W3 implementation object is not a commit")
    if _git(root, "cat-file", "-t", expected_receipt_head_sha) != "commit":
        raise W3ProvenanceError("W3 receipt HEAD object is not a commit")
    if _git(root, "rev-parse", "HEAD") != runtime_sha:
        raise W3ProvenanceError("W3 checkout HEAD does not match the runtime pin")
    if not _git_succeeds(
        root,
        "merge-base",
        "--is-ancestor",
        expected_implementation_sha,
        expected_receipt_head_sha,
    ):
        raise W3ProvenanceError("W3 implementation is not an ancestor of receipt HEAD")
    if not _git_succeeds(
        root,
        "merge-base",
        "--is-ancestor",
        expected_receipt_head_sha,
        runtime_sha,
    ):
        raise W3ProvenanceError("W3 receipt HEAD is not an ancestor of the runtime pin")

    readiness = _load_readiness(root / READINESS_PATH)
    if readiness.get("w3_full_sha") != expected_implementation_sha:
        raise W3ProvenanceError("W3 readiness implementation pin does not match")
    if readiness.get("contract") != EXPECTED_CONTRACT:
        raise W3ProvenanceError("W3 readiness contract does not match")
    retention = readiness.get("retention")
    if (
        not isinstance(retention, dict)
        or retention.get("policy_revision") != EXPECTED_POLICY_REVISION
    ):
        raise W3ProvenanceError("W3 readiness policy revision does not match")

    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise W3ProvenanceError("W3 working tree is not clean")
    runtime_drift = _git(
        root,
        "diff",
        "--name-only",
        expected_implementation_sha,
        expected_receipt_head_sha,
        "--",
        *ARCHIVE_PATHS,
    )
    if runtime_drift:
        raise W3ProvenanceError("W3 runtime source drift exists after the implementation pin")

    archive_paths = _archive_manifest(root, runtime_sha)
    return {
        "status": "ok",
        "contract": EXPECTED_CONTRACT,
        "policy_revision": EXPECTED_POLICY_REVISION,
        "implementation_sha": expected_implementation_sha,
        "receipt_head_sha": expected_receipt_head_sha,
        "runtime_sha": runtime_sha,
        "runtime_drift": False,
        "worktree_clean": True,
        "archive_paths": archive_paths,
        "network_calls": 0,
    }


def _archive_manifest(root: Path, implementation_sha: str) -> list[str]:
    output = _git(
        root,
        "ls-tree",
        "-r",
        "--name-only",
        implementation_sha,
        "--",
        *ARCHIVE_PATHS,
    )
    paths = sorted(path for path in output.splitlines() if path)
    required = {"pyproject.toml", "uv.lock", "specs/001-source-knowledge-validation/spec.md"}
    if not required.issubset(paths):
        raise W3ProvenanceError("W3 archive is missing required build metadata")
    if not any(path.startswith("src/w3_knowledge/") for path in paths):
        raise W3ProvenanceError("W3 archive is missing runtime source")
    forbidden = (".git/", ".venv/", ".env", "venv/")
    if any(path.startswith(forbidden) for path in paths):
        raise W3ProvenanceError("W3 archive allowlist contains a forbidden path")
    return paths


def _load_readiness(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise W3ProvenanceError("W3 readiness metadata is unreadable") from error
    if not isinstance(value, dict):
        raise W3ProvenanceError("W3 readiness metadata must be an object")
    return value


def _validate_expected_sha(value: str, label: str) -> None:
    if not _FULL_SHA.fullmatch(value):
        raise W3ProvenanceError(f"expected W3 {label} SHA is invalid")


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise W3ProvenanceError("W3 Git provenance command failed") from error
    return completed.stdout.strip()


def _git_succeeds(root: Path, *arguments: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise W3ProvenanceError("W3 Git provenance command failed") from error
    if completed.returncode not in {0, 1}:
        raise W3ProvenanceError("W3 Git provenance command failed")
    return completed.returncode == 0


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--w3-root", type=Path, default=repo_root / "w3" / "Project_EPICK_Service"
    )
    parser.add_argument(
        "--expected-implementation-sha", default=EXPECTED_IMPLEMENTATION_SHA
    )
    parser.add_argument("--expected-receipt-head-sha", default=EXPECTED_RECEIPT_HEAD_SHA)
    parser.add_argument("--expected-runtime-sha", default=EXPECTED_RUNTIME_SHA)
    parser.add_argument("--expected-remote", default=EXPECTED_REMOTE)
    args = parser.parse_args()
    result = verify_w3_runtime_provenance(
        w3_root=args.w3_root,
        expected_implementation_sha=args.expected_implementation_sha,
        expected_receipt_head_sha=args.expected_receipt_head_sha,
        expected_runtime_sha=args.expected_runtime_sha,
        expected_remote=args.expected_remote,
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except W3ProvenanceError as error:
        raise SystemExit(str(error)) from error
