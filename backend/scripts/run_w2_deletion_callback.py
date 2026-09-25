"""Run the private W2 deletion callback with the isolated deletion DB login."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import uvicorn  # noqa: E402

from app.runtime.w2_deletion_callback import (  # noqa: E402
    create_configured_w2_deletion_callback_app,
)


def main() -> None:
    uvicorn.run(create_configured_w2_deletion_callback_app(), host="0.0.0.0", port=8081)


if __name__ == "__main__":
    main()
