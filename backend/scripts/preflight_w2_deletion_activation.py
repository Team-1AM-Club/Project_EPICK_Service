"""Mint a short-lived W1 deletion relay proof from the actual W2 DB head.

Run this one-time operator check immediately before starting the dedicated W1
deletion relay. The W2 DB URL is read only by this process, not the relay.
The source SHA must be independently read from the selected image's OCI label.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.runtime.integration_preflight import (  # noqa: E402
    IntegrationPreflightError,
    create_w2_deletion_activation_proof,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    db_url = os.environ.get("W2_DELETION_PREFLIGHT_DATABASE_URL")
    source_sha = os.environ.get("W2_DELETION_SOURCE_SHA")
    image_digest = os.environ.get("W2_DELETION_IMAGE_DIGEST")
    if not all((db_url, source_sha, image_digest)):
        raise SystemExit("W2_DELETION_PREFLIGHT_INPUTS_MISSING")

    engine = None
    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as connection:
            proof = create_w2_deletion_activation_proof(
                connection,
                observed_source_sha=source_sha,
                image_digest=image_digest,
                checked_at=datetime.now(UTC),
            )
        output = args.output.resolve(strict=False)
        if not output.parent.is_dir():
            raise IntegrationPreflightError("W2 deletion proof directory is missing")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=output.parent, delete=False
            ) as handle:
                temporary = handle.name
                os.chmod(temporary, 0o600)
                json.dump(proof, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            os.replace(temporary, output)
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
    except Exception as error:
        raise SystemExit("W2_DELETION_ACTIVATION_PREFLIGHT_FAILED") from error
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps({"status": "PREFLIGHT_PASSED", "scope": "w2_deletion_v2"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
