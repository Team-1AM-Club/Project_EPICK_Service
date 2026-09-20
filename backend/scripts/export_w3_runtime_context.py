"""Export an allowlisted W3 build context from an immutable local Git commit."""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from scripts.verify_w3_runtime_provenance import W3ProvenanceError
except ModuleNotFoundError:  # Direct script execution from backend/scripts.
    from verify_w3_runtime_provenance import W3ProvenanceError

EXPECTED_IMPLEMENTATION_SHA = "3b23e0843a134fb341e6a256576ccf52fedbf4a8"
ARCHIVE_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "src",
    "tests",
    "specs/001-source-knowledge-validation/spec.md",
)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def export_w3_runtime_context(
    *, source: Path, commit: str, destination: Path
) -> dict[str, Any]:
    source_root = source.resolve()
    destination_root = destination.resolve()
    if not _FULL_SHA.fullmatch(commit):
        raise W3ProvenanceError("W3 build-context commit SHA is invalid")
    if not source_root.is_dir():
        raise W3ProvenanceError("W3 build-context source is unavailable")
    if destination_root.exists():
        raise W3ProvenanceError("W3 build-context destination already exists")
    if _git(source_root, "cat-file", "-t", commit) != "commit":
        raise W3ProvenanceError("W3 build-context object is not a commit")

    archive = _git_archive(source_root, commit)
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".w3-context-", dir=destination_root.parent
    ) as temporary:
        export_root = Path(temporary) / "context"
        export_root.mkdir()
        archive_paths = _extract_allowlisted(archive, export_root)
        manifest = {
            "schema_version": "w1-w3.build-context.v1",
            "commit": commit,
            "file_count": len(archive_paths),
            "archive_paths": archive_paths,
            "network_calls": 0,
        }
        (export_root / ".w3-build-provenance.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        export_root.replace(destination_root)
    return manifest


def _git_archive(root: Path, commit: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "archive", "--format=tar", commit, *ARCHIVE_PATHS],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise W3ProvenanceError("W3 build-context archive command failed") from error
    return completed.stdout


def _extract_allowlisted(archive: bytes, destination: Path) -> list[str]:
    files: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            members = bundle.getmembers()
            for member in members:
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or not _is_allowlisted(path):
                    raise W3ProvenanceError("W3 build-context archive contains a forbidden path")
                if not (member.isdir() or member.isfile()):
                    raise W3ProvenanceError("W3 build-context archive contains a link or device")
                if member.isfile():
                    files.append(path.as_posix())
            bundle.extractall(destination, members=members)
    except (tarfile.TarError, OSError) as error:
        raise W3ProvenanceError("W3 build-context archive is unreadable") from error
    required = {"pyproject.toml", "uv.lock", "specs/001-source-knowledge-validation/spec.md"}
    if not required.issubset(files) or not any(
        path.startswith("src/w3_knowledge/") for path in files
    ):
        raise W3ProvenanceError("W3 build-context archive is incomplete")
    return sorted(files)


def _is_allowlisted(path: PurePosixPath) -> bool:
    value = path.as_posix().rstrip("/")
    return (
        value in {"pyproject.toml", "uv.lock", "src", "tests", "specs"}
        or value == "specs/001-source-knowledge-validation"
        or value == "specs/001-source-knowledge-validation/spec.md"
        or value.startswith("src/")
        or value.startswith("tests/")
    )


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise W3ProvenanceError("W3 build-context Git command failed") from error
    return completed.stdout.strip()


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", type=Path, default=repo_root / "w3" / "Project_EPICK_Service"
    )
    parser.add_argument("--commit", default=EXPECTED_IMPLEMENTATION_SHA)
    parser.add_argument(
        "--destination", type=Path, default=repo_root / ".runtime" / "w3-build-context"
    )
    args = parser.parse_args()
    result = export_w3_runtime_context(
        source=args.source,
        commit=args.commit,
        destination=args.destination,
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except W3ProvenanceError as error:
        raise SystemExit(str(error)) from error
