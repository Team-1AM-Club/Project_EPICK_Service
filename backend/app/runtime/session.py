from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.registry import load_all_models


def create_worker_session_factory() -> sessionmaker:
    """Create a worker-only DB factory; the public API `DATABASE_URL` is never a fallback."""

    if not settings.worker_database_url:
        raise RuntimeError("WORKER_DATABASE_URL must be set before starting a W1 worker")
    load_all_models()
    worker_engine = create_engine(settings.worker_database_url, pool_pre_ping=True)
    return sessionmaker(bind=worker_engine, autoflush=False, expire_on_commit=False)
