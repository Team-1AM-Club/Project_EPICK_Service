from __future__ import annotations

import uvicorn

from app.api.private.w4_recommendations import create_configured_w4_recommendation_private_app


def main() -> None:
    uvicorn.run(
        create_configured_w4_recommendation_private_app(),
        host="0.0.0.0",  # noqa: S104 - listener is isolated by the private compose network
        port=8012,
        access_log=False,
    )


if __name__ == "__main__":
    main()
