from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.export_w3_runtime_context import export_w3_runtime_context
from scripts.verify_w3_runtime_provenance import (
    W3ProvenanceError,
    verify_w3_runtime_provenance,
)

REPO_ROOT = Path(__file__).parents[3]
W3_ROOT = REPO_ROOT / "w3" / "Project_EPICK_Service"
EXPECTED_IMPLEMENTATION_SHA = "3b23e0843a134fb341e6a256576ccf52fedbf4a8"
EXPECTED_RECEIPT_HEAD_SHA = "34660343f197c74cc03459a93e0160e46adbcd2b"
EXPECTED_REMOTE = "https://github.com/Team-1AM-Club/Project_EPICK_Service.git"


@pytest.mark.skipif(
    not (W3_ROOT / ".git").exists(),
    reason="independent W3 Git clone is not available in this CI job",
)
def test_received_w3_clone_matches_pins_without_runtime_drift() -> None:
    result = verify_w3_runtime_provenance(
        w3_root=W3_ROOT,
        expected_implementation_sha=EXPECTED_IMPLEMENTATION_SHA,
        expected_receipt_head_sha=EXPECTED_RECEIPT_HEAD_SHA,
        expected_remote=EXPECTED_REMOTE,
    )

    assert result["status"] == "ok"
    assert result["implementation_sha"] == EXPECTED_IMPLEMENTATION_SHA
    assert result["receipt_head_sha"] == EXPECTED_RECEIPT_HEAD_SHA
    assert result["contract"] == "w3.private.core-decision/0.1-candidate"
    assert result["policy_revision"] == "w3.retention/1.1"
    assert result["runtime_drift"] is False
    assert result["worktree_clean"] is True
    assert result["network_calls"] == 0
    assert all(not path.startswith((".git/", ".venv/")) for path in result["archive_paths"])


def test_synthetic_clone_exercises_commit_ancestry_and_archive_allowlist(
    tmp_path: Path,
) -> None:
    implementation_sha, receipt_head_sha = _write_git_fixture(tmp_path)

    result = verify_w3_runtime_provenance(
        w3_root=tmp_path,
        expected_implementation_sha=implementation_sha,
        expected_receipt_head_sha=receipt_head_sha,
        expected_remote=EXPECTED_REMOTE,
    )

    assert result["runtime_drift"] is False
    assert result["archive_paths"] == [
        "pyproject.toml",
        "specs/001-source-knowledge-validation/spec.md",
        "src/w3_knowledge/__init__.py",
        "tests/test_smoke.py",
        "uv.lock",
    ]
    assert ".env" not in result["archive_paths"]


def test_provenance_rejects_wrong_remote(tmp_path: Path) -> None:
    implementation_sha, receipt_head_sha = _write_git_fixture(tmp_path)

    with pytest.raises(W3ProvenanceError, match="remote"):
        verify_w3_runtime_provenance(
            w3_root=tmp_path,
            expected_implementation_sha=implementation_sha,
            expected_receipt_head_sha=receipt_head_sha,
            expected_remote="https://example.invalid/wrong.git",
        )


def test_provenance_rejects_dirty_worktree(tmp_path: Path) -> None:
    implementation_sha, receipt_head_sha = _write_git_fixture(tmp_path)
    (tmp_path / "src" / "w3_knowledge" / "__init__.py").write_text(
        "dirty = True\n", encoding="utf-8"
    )

    with pytest.raises(W3ProvenanceError, match="working tree"):
        verify_w3_runtime_provenance(
            w3_root=tmp_path,
            expected_implementation_sha=implementation_sha,
            expected_receipt_head_sha=receipt_head_sha,
            expected_remote=EXPECTED_REMOTE,
        )


def test_provenance_rejects_runtime_drift_after_implementation(tmp_path: Path) -> None:
    implementation_sha, receipt_head_sha = _write_git_fixture(tmp_path, runtime_drift=True)

    with pytest.raises(W3ProvenanceError, match="runtime source drift"):
        verify_w3_runtime_provenance(
            w3_root=tmp_path,
            expected_implementation_sha=implementation_sha,
            expected_receipt_head_sha=receipt_head_sha,
            expected_remote=EXPECTED_REMOTE,
        )


def test_provenance_rejects_readiness_pin_mismatch(tmp_path: Path) -> None:
    implementation_sha, receipt_head_sha = _write_git_fixture(tmp_path)
    readiness_path = tmp_path / "contracts" / "core-runtime" / "readiness.json"
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    readiness["w3_full_sha"] = "0" * 40
    readiness_path.write_text(json.dumps(readiness), encoding="utf-8")

    with pytest.raises(W3ProvenanceError, match="readiness implementation pin"):
        verify_w3_runtime_provenance(
            w3_root=tmp_path,
            expected_implementation_sha=implementation_sha,
            expected_receipt_head_sha=receipt_head_sha,
            expected_remote=EXPECTED_REMOTE,
        )


def test_build_context_export_uses_only_the_allowlisted_commit_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    implementation_sha, _ = _write_git_fixture(source)
    destination = tmp_path / "export"

    result = export_w3_runtime_context(
        source=source,
        commit=implementation_sha,
        destination=destination,
    )

    assert result["commit"] == implementation_sha
    assert result["file_count"] == 5
    assert (destination / "pyproject.toml").is_file()
    assert (destination / "src" / "w3_knowledge" / "__init__.py").is_file()
    assert not (destination / ".git").exists()
    assert not (destination / ".venv").exists()
    assert not (destination / ".env").exists()
    manifest = json.loads(
        (destination / ".w3-build-provenance.json").read_text(encoding="utf-8")
    )
    assert manifest == result


def test_build_context_export_refuses_to_overwrite_existing_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    implementation_sha, _ = _write_git_fixture(source)
    destination = tmp_path / "export"
    destination.mkdir()

    with pytest.raises(W3ProvenanceError, match="destination already exists"):
        export_w3_runtime_context(
            source=source,
            commit=implementation_sha,
            destination=destination,
        )


def _write_git_fixture(root: Path, *, runtime_drift: bool = False) -> tuple[str, str]:
    _git(root, "init")
    _git(root, "config", "user.email", "synthetic@example.invalid")
    _git(root, "config", "user.name", "Synthetic Test")
    _git(root, "remote", "add", "origin", EXPECTED_REMOTE)
    _write(root / "pyproject.toml", "[project]\nname='w3-fixture'\nversion='0.0.0'\n")
    _write(root / "uv.lock", "version = 1\n")
    _write(root / "src" / "w3_knowledge" / "__init__.py", "VALUE = 1\n")
    _write(root / "tests" / "test_smoke.py", "def test_smoke(): assert True\n")
    _write(root / "specs" / "001-source-knowledge-validation" / "spec.md", "# Fixture\n")
    _write(root / ".env", "MUST_NOT_ENTER_ARCHIVE=1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "runtime implementation")
    implementation_sha = _git(root, "rev-parse", "HEAD")

    readiness = {
        "w3_full_sha": implementation_sha,
        "contract": "w3.private.core-decision/0.1-candidate",
        "retention": {"policy_revision": "w3.retention/1.1"},
    }
    _write(
        root / "contracts" / "core-runtime" / "readiness.json",
        json.dumps(readiness),
    )
    _write(root / "docs" / "receipt.md", "documentation-only receipt\n")
    if runtime_drift:
        _write(root / "src" / "w3_knowledge" / "__init__.py", "VALUE = 2\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "receipt")
    return implementation_sha, _git(root, "rev-parse", "HEAD")


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
