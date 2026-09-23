from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.sources import Source
from app.services.source_url_policy import CanonicalSourceUrl


class SourceCollectionRepository:
    """Resolve canonical public Sources without duplicating concurrent requests."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve_or_create(
        self,
        *,
        company_id: UUID,
        source_type: str,
        canonical: CanonicalSourceUrl,
        checked_at: datetime,
    ) -> tuple[Source, bool]:
        inserted_id = self.session.scalar(
            insert(Source)
            .values(
                company_id=company_id,
                source_type=source_type,
                canonical_url=canonical.url,
                canonical_url_hash=canonical.digest,
                url_normalization_version=canonical.normalization_version,
                policy_version="w1-source-registration-v1",
                policy_checked_at=checked_at,
            )
            .on_conflict_do_nothing(
                index_elements=[Source.company_id, Source.canonical_url_hash]
            )
            .returning(Source.id)
        )
        source = self.session.scalar(
            select(Source).where(
                Source.company_id == company_id,
                Source.canonical_url_hash == canonical.digest,
            )
        )
        if source is None:
            raise RuntimeError("canonical Source could not be resolved")
        return source, inserted_id is not None
