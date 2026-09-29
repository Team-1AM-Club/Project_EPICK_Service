"""Package the current source and selected synthetic evidence; verify every ZIP member."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    paths = []
    for folder in ("epick_w4", "examples", "tests", "scripts", "schemas", "samples", "docs", ".github"):
        paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
                     and p.suffix in {".py", ".json", ".md", ".yml", ".yaml", ".ps1"})
    paths += [ROOT / p for p in ("README.md", "pyproject.toml", "uv.lock", ".gitignore", ".gitattributes")]
    evidence = ROOT / "output/c01-staged-evaluation-20260918"
    for folder in ("run-1", "run-2", "final-demo", "w1-recheck", "verification-final", "audit-final"):
        paths.extend(p for p in (evidence / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts
                     and p.suffix in {".json", ".py", ".md"})
    paths += [ROOT / "output/c01-domain-evaluation-20260918/run-3/actual-extraction.json"]
    entries = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in sorted(set(paths))}
    entries["00-START-HERE.md"] = ("# W4 전달 자료 · 2026-09-18\n\n"
        "[파일별 기능과 실행법](docs/w4-team-files-2026-09-18.md)부터 확인하세요.\n\n"
        "[모델 비교·실제 시연 결과](docs/w4-staged-result-2026-09-18.md) · "
        "[W1 연결 계약](docs/w4-w1-bridge-contract-2026-09-18.md)\n\n"
        "351개 로컬 테스트 통과. 실제 모델 호출 156회와 별도 중단 이력 12회. "
        "운영 모델 선정은 보류이며 실제 팀 서버·PostgreSQL은 미연결입니다.\n").encode("utf-8")
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}
    entries["MANIFEST.json"] = (json.dumps({"schema_version": "w4-handoff-manifest/0.1", "sha256": manifest},
                                         ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # The latest entry documents must resolve inside the artifact. Historical
    # reports may link to earlier output archives deliberately excluded here.
    link_count = 0
    for name in ("00-START-HERE.md", "docs/w4-team-files-2026-09-18.md", "docs/w4-staged-result-2026-09-18.md",
                 "docs/w4-w1-bridge-contract-2026-09-18.md"):
        for link in re.findall(r"\]\(([^)]+)\)", entries[name].decode("utf-8")):
            if "://" in link or link.startswith("#"):
                continue
            target = (ROOT / Path(name).parent / link.split("#")[0]).resolve().relative_to(ROOT.resolve()).as_posix()
            if target not in entries:
                raise ValueError("Unpackaged current-document link: " + target)
            link_count += 1
    archive = out / "EPICK_W4_Staged_W1_Handoff_2026-09-18.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for name, data in entries.items():
            package.writestr(name, data)
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == set(entries)
        assert all(hashlib.sha256(package.read(name)).hexdigest() == sha for name, sha in manifest.items())
    report = {"archive": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "bytes": archive.stat().st_size, "files": len(entries), "manifest_verified": len(manifest),
              "zip_crc_passed": True, "current_document_links_verified": link_count,
              "includes_models_credentials_virtualenv_git_databases": False}
    (out / "package-verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    guide = entries["docs/w4-team-files-2026-09-18.md"].decode("utf-8").replace("](w4-", "](../../docs/w4-")
    (out / "FILE-GUIDE.md").write_text(guide, encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
