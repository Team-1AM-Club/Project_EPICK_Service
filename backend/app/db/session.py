from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield one transaction and always return its connection cleanly to the pool."""
    session = SessionLocal()
    try:
        with session.begin():
            yield session
    finally:
        session.close()


def set_local_owner_context(session: Session, owner_user_id: UUID | str) -> None:
    """Set the RLS owner only for the active transaction.

    PostgreSQL clears set_config(..., true) at transaction completion, which prevents a pooled
    connection from inheriting one request's owner context in the next request.
    """
    if not session.in_transaction():
        raise RuntimeError("owner context requires an active transaction")

    try:
        owner_uuid = UUID(str(owner_user_id))
    except (TypeError, ValueError) as error:
        raise ValueError("owner_user_id must be a valid UUID") from error

    session.execute(
        text("SELECT set_config('app.current_user_id', :owner_user_id, true)"),
        {"owner_user_id": str(owner_uuid)},
    )
