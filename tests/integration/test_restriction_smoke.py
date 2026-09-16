import json
from pathlib import Path
import subprocess
import sys


def test_packaged_command_runs_real_http_process_without_mock(tmp_path):
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run(
        [sys.executable, str(root / "scripts/restriction_smoke.py"), "--output-dir", str(tmp_path)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, (run.stdout, run.stderr)
    report = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert report["passed"] == report["total"]
    assert report["total"] >= 12
    assert report["transport"] == "real-http-subprocess"
    assert report["team_contract_approved"] is False
    assert (tmp_path / "w3.sqlite").is_file()
    assert (tmp_path / "w4.sqlite").is_file()
