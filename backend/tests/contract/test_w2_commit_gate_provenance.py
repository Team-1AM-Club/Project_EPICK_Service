from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
ENGINE_ROOT = PROJECT_ROOT / "w2" / "Project_EPICK_Engine"
SCRIPT = BACKEND_ROOT / "scripts" / "verify_w2_commit_gate_provenance.py"
MANIFEST = ENGINE_ROOT / "contracts" / "w2-private" / "artifacts.sha256"
W2_SHA = "16a7bd2653873a20a563e6d2f54c24c6dc18c373"


def _run(*, manifest: Path = MANIFEST) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--engine-root",
            str(ENGINE_ROOT),
            "--revision",
            W2_SHA,
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


def test_provenance_verifier_accepts_all_pinned_git_blobs() -> None:
    completed = _run()

    assert completed.returncode == 0, completed.stderr
    assert _result(completed) == {
        "matched": 81,
        "mismatched": [],
        "missing": [],
        "revision": W2_SHA,
        "status": "ok",
        "total": 81,
    }


def test_provenance_verifier_reports_bounded_digest_mismatch(tmp_path: Path) -> None:
    manifest = tmp_path / "mismatch.sha256"
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    first = next(index for index, line in enumerate(lines) if line and not line.startswith("#"))
    _, path = lines[first].split(maxsplit=1)
    lines[first] = f"{'0' * 64} {path}"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    completed = _run(manifest=manifest)
    result = _result(completed)

    assert completed.returncode == 1
    assert result["status"] == "failed"
    assert result["matched"] == 80
    assert result["mismatched"] == [path]
    assert result["missing"] == []


def test_provenance_verifier_reports_missing_git_blob(tmp_path: Path) -> None:
    manifest = tmp_path / "missing.sha256"
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    first = next(index for index, line in enumerate(lines) if line and not line.startswith("#"))
    digest, _ = lines[first].split(maxsplit=1)
    missing_path = "contracts/w2-private/not-present-for-provenance-test.json"
    lines[first] = f"{digest} {missing_path}"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")

    completed = _run(manifest=manifest)
    result = _result(completed)

    assert completed.returncode == 1
    assert result["status"] == "failed"
    assert result["matched"] == 80
    assert result["mismatched"] == []
    assert result["missing"] == [missing_path]
