"""Download pinned official GGUF weights and portable Windows llama.cpp to TEMP."""

import hashlib
import json
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(tempfile.gettempdir()) / "epick-w4-local-models-b10859"


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def download(item, url):
    target = CACHE / item["filename"]
    if target.exists():
        if target.stat().st_size != item["size"] or sha256(target) != item["sha256"]:
            raise ValueError("Existing file does not match the pinned artifact: " + target.name)
        print("VERIFIED " + target.name, flush=True)
        return target
    partial = target.with_suffix(target.suffix + ".partial")
    request = Request(url, headers={"User-Agent": "EPICK-W4-local-evaluation"})
    print("DOWNLOAD " + target.name, flush=True)
    with urlopen(request, timeout=60) as response, partial.open("wb") as stream:
        total = 0
        next_progress = 256 * 1024 * 1024
        while chunk := response.read(8 * 1024 * 1024):
            total += len(chunk)
            if total > item["size"]:
                raise ValueError("Download exceeded pinned size")
            stream.write(chunk)
            if total >= next_progress:
                print(f"{target.name}: {total // (1024 * 1024)} MiB", flush=True)
                next_progress += 256 * 1024 * 1024
    if total != item["size"] or sha256(partial) != item["sha256"]:
        raise ValueError("Download checksum/size mismatch: " + target.name)
    partial.replace(target)
    print("VERIFIED " + target.name, flush=True)
    return target


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "samples/evaluation/local-models.json").read_text(encoding="utf-8"))
    runtime = CACHE / "runtime"
    runtime.mkdir(exist_ok=True)
    for item in manifest["runtime"]["archives"]:
        archive = download(item, item["url"])
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                if not (runtime / member.filename).resolve().is_relative_to(runtime.resolve()):
                    raise ValueError("Archive path outside runtime directory")
            package.extractall(runtime)
    for item in manifest["models"]:
        url = f"https://huggingface.co/{item['repository']}/resolve/{item['revision']}/{item['filename']}"
        download(item, url)
    print(json.dumps({"cache": str(CACHE), "status": "VERIFIED"}), flush=True)


if __name__ == "__main__":
    main()
