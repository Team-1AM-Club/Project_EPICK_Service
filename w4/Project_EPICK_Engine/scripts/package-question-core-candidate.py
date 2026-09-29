"""Package candidate-source review, implementation, and reproducible local results."""

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "output/question-core-candidate-20260919"
DOCS = (
    "docs/w4-required-contracts-reply-2026-09-19-r3.md",
    "docs/w4-question-core-candidate-handoff-2026-09-19.md",
    "docs/w4-question-core-producer.md",
    "docs/w4-question-core-ct12-runbook-draft.md",
)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    provenance = read(ROOT / "samples/question-core-candidate-20260919/source-provenance.json")
    local, full = (read(EVIDENCE / stage / "summary.json") for stage in ("local", "full"))
    if (
        provenance["candidate_originals_verified"] != 2
        or provenance["contract_adopted"]
        or local["status"] != "PASSED_LOCAL_ONLY"
        or local["tests"]["passed"] < 73
        or full["status"] != "PASSED"
        or full["tests"]["passed"] < 454
    ):
        raise ValueError("Candidate verification is incomplete")
    counts = {}
    for stage in ("local", "full"):
        hashes = read(EVIDENCE / stage / "source-sha256.json")
        if any(sha((ROOT / path).read_bytes()) != expected for path, expected in hashes.items()):
            raise ValueError("Source changed after " + stage + " verification")
        counts[stage] = len(hashes)
    for path, expected in provenance["files"].items():
        if sha((ROOT / path).read_bytes()) != expected:
            raise ValueError("Received candidate provenance changed")

    paths = []
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
            and p.suffix in {".py", ".json", ".md", ".yml", ".yaml", ".ps1", ".sha256"}
        )
    paths.extend(
        ROOT / p
        for p in (
            "README.md",
            "pyproject.toml",
            "uv.lock",
            ".gitignore",
            ".gitattributes",
            "docs/inputs/w1-runtime-status-reported-20260919.png",
        )
    )
    for folder in (
        EVIDENCE,
        ROOT / "output/question-core-producer-20260919",
        ROOT / "output/question-core-review-20260919",
    ):
        paths.extend(p for p in folder.rglob("*") if p.is_file() and p.suffix in {".json", ".log"})
    entries = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in sorted(set(paths))}
    entries["00-START-HERE.md"] = (
        "# W4 후보 원문 검토·구현 자료 · 2026-09-19 r3\n\n"
        "[항목별 회신](docs/w4-required-contracts-reply-2026-09-19-r3.md) · "
        "[파일별 안내](docs/w4-question-core-candidate-handoff-2026-09-19.md)\n\n"
        "후보 원문 2개 checksum 일치, candidate schema 원문을 보존한 codec 수정. "
        f"producer {local['tests']['passed']}개 / 전체 {full['tests']['passed']}개 로컬 통과.\n\n"
        "W4 검토 회신 초안이며 양측 채택·운영 정책 승인·실제 context/queue·공동 CT-12는 미완료입니다. "
        "과거 r1/r2 문서의 원문 미수령 상태는 역사적 기록입니다. "
        "SOURCE-STATE.json과 MANIFEST.json을 확인하세요. ZIP은 접근 가능한 commit SHA를 대신하지 않습니다.\n"
    ).encode("utf-8")

    def stats(report):
        return {
            k: report["tests"][k]
            for k in ("status", "tests_run", "passed", "failures", "errors", "skipped")
        }

    entries["SOURCE-STATE.json"] = encoded(
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "W1_CANDIDATE_VERIFIED_W4_REPLY_DRAFT_LOCAL_RUNTIME_ONLY",
            "candidate_originals_received": 2,
            "candidate_originals_hash_verified": True,
            "candidate_schema_modified": False,
            "candidate_source_commit_sha": None,
            "w1_existing_baseline_sha": provenance["w1_existing_baseline_sha"],
            "w4_accessible_producer_sha": None,
            "contract_adopted": False,
            "policy_production_approved": False,
            "candidate_version_external_send_allowed": False,
            "current_local_tests": stats(local),
            "current_full_tests": stats(full),
            "candidate_restart_demo": local["restart_demo"]["status"],
            "actual_sqs_requests": 0,
            "real_model_calls": 0,
            "live_w1_context_integration": "NOT_CONNECTED",
            "w1_aws_setup": "TEAM_REPORTED_COMPLETE_NOT_INDEPENDENTLY_VERIFIED",
            "joint_ct12": "NOT_RUN",
            "external_messages_sent": 0,
            "source_hashes_checked": counts,
        }
    )
    links = 0
    for name in ("00-START-HERE.md", *DOCS):
        for link in re.findall(r"\]\(([^)]+)\)", entries[name].decode("utf-8")):
            if "://" in link or link.startswith("#"):
                continue
            target = (
                (ROOT / Path(name).parent / link.split("#")[0])
                .resolve()
                .relative_to(ROOT.resolve())
                .as_posix()
            )
            if target not in entries:
                raise ValueError("Missing current document link: " + target)
            links += 1
    manifest = {name: sha(raw) for name, raw in entries.items()}
    entries["MANIFEST.json"] = encoded({"sha256": manifest})
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    archive = out / "EPICK_W4_Candidate_Review_2026-09-19_r3.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for name, data in entries.items():
            package.writestr(name, data)
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None or set(package.namelist()) != set(entries):
            raise ValueError("Archive content/CRC mismatch")
        if any(sha(package.read(path)) != expected for path, expected in manifest.items()):
            raise ValueError("Archive manifest mismatch")
    report = {
        "archive": archive.name,
        "sha256": sha(archive.read_bytes()),
        "bytes": archive.stat().st_size,
        "files": len(entries),
        "manifest_verified": len(manifest),
        "zip_crc_passed": True,
        "current_document_links_verified": links,
        "source_hashes_checked": counts,
        "candidate_originals_verified": 2,
        "contract_adopted": False,
        "joint_ct12": "NOT_RUN",
    }
    (out / "package-verification.json").write_bytes(encoded(report))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
