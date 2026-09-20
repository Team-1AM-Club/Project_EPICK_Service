from __future__ import annotations

import uvicorn

from app.runtime.w3_authority_adapter import create_configured_w3_authority_private_app


def main() -> None:
    uvicorn.run(
        create_configured_w3_authority_private_app(),
        host="0.0.0.0",  # Listener is published only on the host loopback interface.
        port=8043,
        access_log=False,
    )


if __name__ == "__main__":
    main()
