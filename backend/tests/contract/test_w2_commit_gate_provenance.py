from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
ENGINE_ROOT = PROJECT_ROOT / "w2" / "Project_EPICK_Engine"
SCRIPT = BACKEND_ROOT / "scripts" / "verify_w2_commit_gate_provenance.py"
MANIFEST = ENGINE_ROOT / "contracts" / "w2-private" / "artifacts.sha256"
W2_SHA = "16a7bd2653873a20a563e6d2f54c24c6dc18c373"
SNAPSHOT_ROOT = BACKEND_ROOT / "contracts" / "fixtures" / "v1" / "w2_commit_gate"
SCHEMA_ROOT = BACKEND_ROOT / "contracts" / "w2" / "v1"


def _run(
    *, engine_root: Path, revision: str, manifest: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--engine-root",
            str(engine_root),
            "--revision",
            revision,
            "--manifest",
            str(manifest),
        ],
        check=False,
        capture_output=True,
        cwd=BACKEND_ROOT,
        text=True,
    )


def _result(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(completed.stdout)


def _sha256_lf(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _create_git_blob_fixture(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """Build the smallest Git repository needed to test the verifier itself.

    GitHub Actions checks out this W1 repository only; it intentionally has no
    W2 worktree or W2 object database.  The adopted schema/fixture snapshot is
    verified below, while this fixture exercises the exact ``git cat-file``
    code path without pretending that CI has W2's private repository.
    """

    engine_root = tmp_path / "engine"
    artifact = engine_root / "contracts" / "w2-private" / "proposal.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"proposal":"pinned"}\n', encoding="utf-8")
    relative_path = artifact.relative_to(engine_root).as_posix()
    manifest = engine_root / "contracts" / "w2-private" / "artifacts.sha256"
    manifest.write_text(f"{_sha256_lf(artifact)} {relative_path}\n", encoding="utf-8")
    for command in (
        ["git", "init", str(engine_root)],
        ["git", "-C", str(engine_root), "add", "."],
        [
            "git",
            "-C",
            str(engine_root),
            "-c",
            "user.name=EPICK test",
            "-c",
            "user.email=epick-test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
    ):
        subprocess.run(command, check=True, capture_output=True)
    revision = subprocess.run(
        ["git", "-C", str(engine_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return engine_root, manifest, revision, relative_path


def test_committed_w2_snapshot_hashes_match_its_manifest() -> None:
    """CI validates every adopted W2 artifact that is actually committed to W1."""

    manifest = json.loads((SNAPSHOT_ROOT / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        assert _sha256_lf(SNAPSHOT_ROOT / entry["file"]) == entry["sha256"]
    for entry in manifest["schemas"]:
        assert _sha256_lf(SCHEMA_ROOT / entry["file"]) == entry["sha256"]


def test_provenance_verifier_accepts_a_git_blob_manifest(tmp_path: Path) -> None:
    engine_root, manifest, revision, _ = _create_git_blob_fixture(tmp_path)
    completed = _run(engine_root=engine_root, revision=revision, manifest=manifest)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == {
        "matched": 1,
        "mismatched": [],
        "missing": [],
        "revision": revision,
        "status": "ok",
        "total": 1,
    }


def test_provenance_verifier_reports_bounded_digest_mismatch(tmp_path: Path) -> None:
    engine_root, _, revision, path = _create_git_blob_fixture(tmp_path)
    manifest = tmp_path / "mismatch.sha256"
    manifest.write_text(f"{'0' * 64} {path}\n", encoding="utf-8")

    completed = _run(engine_root=engine_root, revision=revision, manifest=manifest)
    result = _result(completed)

    assert completed.returncode == 1
    assert result["status"] == "failed"
    assert result["matched"] == 0
    assert result["mismatched"] == [path]
    assert result["missing"] == []


def test_provenance_verifier_reports_missing_git_blob(tmp_path: Path) -> None:
    engine_root, source_manifest, revision, _ = _create_git_blob_fixture(tmp_path)
    manifest = tmp_path / "missing.sha256"
    digest, _ = source_manifest.read_text(encoding="utf-8").split(maxsplit=1)
    missing_path = "contracts/w2-private/not-present-for-provenance-test.json"
    manifest.write_text(f"{digest} {missing_path}\n", encoding="utf-8")

    completed = _run(engine_root=engine_root, revision=revision, manifest=manifest)
    result = _result(completed)

    assert completed.returncode == 1
    assert result["status"] == "failed"
    assert result["matched"] == 0
    assert result["mismatched"] == []
    assert result["missing"] == [missing_path]


@pytest.mark.skipif(not ENGINE_ROOT.is_dir(), reason="W2 checkout is not available in this CI job")
def test_external_w2_checkout_matches_the_81_blob_delivery_manifest() -> None:
    """Run only where the verified W2 Git object database is deliberately supplied."""

    completed = _run(engine_root=ENGINE_ROOT, revision=W2_SHA, manifest=MANIFEST)

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == {
        "matched": 81,
        "mismatched": [],
        "missing": [],
        "revision": W2_SHA,
        "status": "ok",
        "total": 81,
    }
