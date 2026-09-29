"""Package the pre-adoption W4 review with exact source and local evidence."""

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "output/question-core-review-20260919"
CURRENT_DOCS = (
    "docs/w4-required-contracts-reply-2026-09-19-r2.md",
    "docs/w4-question-core-review-handoff-2026-09-19.md",
    "docs/w4-question-core-ct12-runbook-draft.md",
    "docs/w4-question-core-producer.md",
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def encoded(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    baseline = read_json(REVIEW / "baseline/source-review.json")
    local = read_json(REVIEW / "local/summary.json")
    if baseline["status"] != "BASELINE_VERIFIED_PRE_ADOPTION":
        raise ValueError("Baseline verification is incomplete")
    if local["status"] != "PASSED_LOCAL_ONLY" or local["tests"]["tests_run"] < 59:
        raise ValueError("Local producer verification is incomplete")
    hashes = read_json(REVIEW / "local/source-sha256.json")
    for name, expected in hashes.items():
        if sha256((ROOT / name).read_bytes()) != expected:
            raise ValueError("Source changed after verification: " + name)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    paths = []
    allowed = {".py", ".json", ".md", ".yml", ".yaml", ".ps1", ".sha256"}
    for folder in (
        "epick_w4",
        "examples",
        "tests",
        "scripts",
        "schemas",
        "samples",
        "docs",
        ".github",
    ):
        paths.extend(
            p
            for p in (ROOT / folder).rglob("*")
            if p.is_file()
            and not p.is_symlink()
            and "__pycache__" not in p.parts
            and p.suffix in allowed
        )
    paths.extend(
        ROOT / name
        for name in ("README.md", "pyproject.toml", "uv.lock", ".gitignore", ".gitattributes")
    )
    for folder in (REVIEW, ROOT / "output/question-core-producer-20260919"):
        paths.extend(
            p
            for p in folder.rglob("*")
            if p.is_file() and not p.is_symlink() and p.suffix in {".json", ".log"}
        )
    entries = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in sorted(set(paths))}
    entries["00-START-HERE.md"] = (
        "# W4 계약 합의 전 검토 자료 · 2026-09-19 r2\n\n"
        "[항목별 회신](docs/w4-required-contracts-reply-2026-09-19-r2.md) · "
        "[파일별 안내](docs/w4-question-core-review-handoff-2026-09-19.md)\n\n"
        "기존 W1 기준 체크섬 3개와 경계 검사 11개 통과. "
        f"이번 producer 검사 {local['tests']['passed']}개와 로컬 복구 시연 통과.\n\n"
        "후보 원문·양측 합의·실제 context/queue/IAM·공동 CT-12는 미완료입니다. "
        "ZIP은 commit full SHA를 대신하지 않습니다. SOURCE-STATE.json을 확인하세요.\n"
    ).encode("utf-8")
    entries["SOURCE-STATE.json"] = encoded(
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "PRE_ADOPTION_REVIEW_LOCAL_ONLY",
            "source_branch": "feat/w4-recommendation-pipeline",
            "local_base_sha": "d9e31822e2a031b41a303f05ad1e96d29fa09d8f",
            "w4_accessible_producer_sha": None,
            "w1_existing_baseline_sha": baseline["baseline_full_sha"],
            "candidate_originals_received": 0,
            "contract_adopted": False,
            "policy_production_approved": False,
            "baseline_files_verified": baseline["baseline_checksums_verified"],
            "baseline_boundary_checks_passed": baseline["boundary_checks_passed"],
            "current_local_tests": {
                k: local["tests"][k]
                for k in ("status", "tests_run", "passed", "failures", "errors", "skipped")
            },
            "current_restart_demo": local["restart_demo"]["status"],
            "previous_full_suite": {
                "path": "output/question-core-producer-20260919/full/summary.json",
                "scope": "PREVIOUS_RUN_NOT_RERUN_FOR_THIS_REPLY_REVISION",
            },
            "actual_sqs_requests": 0,
            "real_model_calls": 0,
            "joint_ct12": "NOT_RUN",
            "external_messages_sent": 0,
            "source_hashes_checked": len(hashes),
        }
    )
    links_checked = 0
    for name in ("00-START-HERE.md", *CURRENT_DOCS):
        for link in re.findall(r"\]\(([^)]+)\)", entries[name].decode("utf-8")):
            if "://" in link or link.startswith("#"):
                continue
            target = (ROOT / Path(name).parent / link.split("#")[0]).resolve()
            relative = target.relative_to(ROOT.resolve()).as_posix()
            if relative not in entries:
                raise ValueError("Missing linked artifact: " + relative)
            links_checked += 1
    manifest = {name: sha256(data) for name, data in entries.items()}
    entries["MANIFEST.json"] = encoded({"sha256": manifest})
    archive = out / "EPICK_W4_PreAdoption_Review_2026-09-19_r2.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for name, data in entries.items():
            package.writestr(name, data)
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None or set(package.namelist()) != set(entries):
            raise ValueError("ZIP content/CRC mismatch")
        if any(sha256(package.read(name)) != checksum for name, checksum in manifest.items()):
            raise ValueError("ZIP checksum mismatch")
    report = {
        "archive": archive.name,
        "sha256": sha256(archive.read_bytes()),
        "bytes": archive.stat().st_size,
        "files": len(entries),
        "manifest_verified": len(manifest),
        "zip_crc_passed": True,
        "current_document_links_verified": links_checked,
        "source_hashes_checked": len(hashes),
        "contract_adopted": False,
        "joint_ct12": "NOT_RUN",
        "excludes": ["credentials", "models", "virtualenv", "git_metadata", "runtime_databases"],
    }
    (out / "package-verification.json").write_bytes(encoded(report))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
