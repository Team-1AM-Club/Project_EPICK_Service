"""Verify the W2 private commit-gate delivery against immutable Git blob bytes.

The W2 worktree may be checked out with different newline settings.  This verifier deliberately
hashes ``git cat-file blob <revision>:<path>`` bytes rather than worktree files, so provenance is
stable across operating systems.  It prints counts and paths only; fixture bodies never appear in
output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

W2_DELIVERY_SHA = "16a7bd2653873a20a563e6d2f54c24c6dc18c373"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ENGINE_ROOT = _PROJECT_ROOT / "w2" / "Project_EPICK_Engine"
_DEFAULT_MANIFEST = Path("contracts/w2-private/artifacts.sha256")


@dataclass(frozen=True)
class ManifestEntry:
    digest: str
    path: str


def parse_manifest(text: str) -> tuple[ManifestEntry, ...]:
    """Return validated manifest entries while accepting comment and blank lines."""

    entries: list[ManifestEntry] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            digest, path = line.split(maxsplit=1)
        except ValueError as error:
            raise ValueError(f"manifest line {number} is not '<sha256> <path>'") from error
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"manifest line {number} has an invalid SHA-256 digest")
        if not path or path.startswith("-") or Path(path).is_absolute() or ".." in Path(path).parts:
            raise ValueError(f"manifest line {number} has an unsafe Git path")
        entries.append(ManifestEntry(digest=digest, path=path))
    if not entries:
        raise ValueError("manifest has no entries")
    if len({entry.path for entry in entries}) != len(entries):
        raise ValueError("manifest has duplicate paths")
    return tuple(entries)


def _git_blob(*, engine_root: Path, revision: str, path: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(engine_root), "cat-file", "blob", f"{revision}:{path}"],
        check=False,
        capture_output=True,
    )
    if completed.returncode:
        raise FileNotFoundError(path)
    return completed.stdout


def verify_entries(
    *, engine_root: Path, revision: str, entries: Iterable[ManifestEntry]
) -> dict[str, object]:
    """Compare every declared digest with the exact Git blob at ``revision``."""

    matched: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    for entry in entries:
        try:
            actual = hashlib.sha256(
                _git_blob(engine_root=engine_root, revision=revision, path=entry.path)
            ).hexdigest()
        except FileNotFoundError:
            missing.append(entry.path)
            continue
        if actual == entry.digest:
            matched.append(entry.path)
        else:
            mismatched.append(entry.path)
    return {
        "status": "ok" if not missing and not mismatched else "failed",
        "revision": revision,
        "total": len(matched) + len(missing) + len(mismatched),
        "matched": len(matched),
        "missing": missing,
        "mismatched": mismatched,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, default=_DEFAULT_ENGINE_ROOT)
    parser.add_argument("--revision", default=W2_DELIVERY_SHA)
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    engine_root = args.engine_root.resolve()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = engine_root / manifest_path
    try:
        entries = parse_manifest(manifest_path.read_text(encoding="utf-8"))
        result = verify_entries(
            engine_root=engine_root,
            revision=args.revision,
            entries=entries,
        )
    except (OSError, ValueError) as error:
        result = {
            "status": "failed",
            "revision": args.revision,
            "error": type(error).__name__,
        }
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
