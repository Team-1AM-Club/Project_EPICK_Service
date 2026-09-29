"""Build an unpublished CT15 image from one immutable Git archive, not local files."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path


def validate_inputs(source_sha: str, base_image: str, tag: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("full source commit required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}", base_image):
        raise ValueError("immutable base image required")
    if not re.fullmatch(
        r"[a-z0-9][a-z0-9./_-]*(?::[A-Za-z0-9_.-]+)?", tag
    ) or "ct15" not in re.split(r"[/_:.-]", tag):
        raise ValueError("explicit CT15 image tag required")


def build(source_sha: str, base_image: str, tag: str, root: Path) -> dict[str, str]:
    validate_inputs(source_sha, base_image, tag)
    git = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
    resolved = subprocess.check_output(
        [*git, "rev-parse", f"{source_sha}^{{commit}}"], text=True, stderr=subprocess.PIPE
    ).strip()
    if resolved != source_sha:
        raise ValueError("source commit unavailable")
    required = ["Dockerfile.ct15", ".dockerignore", "requirements.ct15.lock"]
    for path in required:
        subprocess.run(
            [*git, "cat-file", "-e", f"{source_sha}:{path}"], check=True, capture_output=True
        )
    with tempfile.TemporaryDirectory(prefix="epick-ct15-build-") as temp:
        archive = Path(temp) / "source.tar"
        subprocess.run(
            [*git, "archive", "--format=tar", f"--output={archive}", source_sha],
            check=True,
            capture_output=True,
        )
        with archive.open("rb") as context:
            subprocess.run(
                [
                    "docker",
                    "build",
                    "--file",
                    "Dockerfile.ct15",
                    "--build-arg",
                    f"PYTHON_IMAGE={base_image}",
                    "--build-arg",
                    f"SOURCE_SHA={source_sha}",
                    "--tag",
                    tag,
                    "-",
                ],
                stdin=context,
                check=True,
            )
    metadata = json.loads(
        subprocess.check_output(
            ["docker", "image", "inspect", tag], text=True, stderr=subprocess.PIPE
        )
    )[0]
    if (
        metadata.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
        != source_sha
    ):
        raise ValueError("image source label mismatch")
    image_id = metadata.get("Id", "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("image identity unavailable")
    return {
        "source_sha": source_sha,
        "base_image": base_image,
        "local_image_id": image_id,
        "status": "BUILT_NOT_PUBLISHED",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    try:
        result = build(
            args.source_sha, args.base_image, args.tag, Path(__file__).resolve().parents[1]
        )
    except Exception:
        print('{"status":"BUILD_FAILED"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
