"""W1-deployable ASGI factory for W2's internal Source authority lookup.

Run with ``uvicorn epick_engine.source_collection.source_authority_operator:create_app
--factory`` behind W1's authenticated network/TLS boundary. No token or DB
connection value is embedded in the application or response.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from epick_engine.source_collection.persistence import (
    create_database_engine,
    create_session_factory,
    database_url_from_environment,
)
from epick_engine.source_collection.source_authority_api import create_source_authority_api


def create_app() -> FastAPI:
    """Bind the internal lookup to W2's authoritative database and W1's secret."""

    token = os.environ.get("EPICK_W2_SOURCE_AUTHORITY_TOKEN", "")
    if not token:
        raise ValueError("Source authority service token is required")

    engine = create_database_engine(database_url_from_environment())
    try:
        app = create_source_authority_api(
            session_factory=create_session_factory(engine), bearer_token=token
        )
    except Exception:
        engine.dispose()
        raise
    app.state.w2_engine = engine
    return app
