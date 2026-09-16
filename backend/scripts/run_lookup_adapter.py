"""Run the W1 private W2 command-lookup adapter."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import uvicorn  # noqa: E402

from app.runtime.lookup_adapter import create_configured_lookup_app  # noqa: E402


def main() -> None:
    uvicorn.run(create_configured_lookup_app(), host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
