from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.jobs import Job
from app.models.sources import JobSourceLink, Source


@dataclass(frozen=True, slots=True)
class W3AuthorityCurrentness:
    job_id: UUID
    company_id: UUID
    source_id: UUID
    analysis_input_version: str | None
    owner_id: UUID
    owner_epoch: int
    job_owner_epoch: int
    owner_status: str
    job_status: str
    linked_analysis_input_version: str | None


class W3AuthorityRepository:
    """Read the smallest authoritative User -> Job -> Source projection required by W3."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_currentness(
        self,
        *,
        job_id: UUID,
        source_id: UUID,
    ) -> W3AuthorityCurrentness | None:
        statement = (
            select(
                Job.id,
                Source.company_id,
                Source.id,
                Job.analysis_input_version,
                User.id,
                User.deletion_epoch,
                Job.owner_deletion_epoch,
                User.account_status,
                Job.status,
                JobSourceLink.analysis_input_version,
            )
            .join(User, User.id == Job.owner_user_id)
            .join(
                JobSourceLink,
                (JobSourceLink.job_id == Job.id)
                & (JobSourceLink.owner_user_id == Job.owner_user_id),
            )
            .join(Source, Source.id == JobSourceLink.source_id)
            .where(Job.id == job_id, Source.id == source_id)
        )
        row = self.session.execute(statement).one_or_none()
        if row is None:
            return None
        return W3AuthorityCurrentness(
            job_id=row[0],
            company_id=row[1],
            source_id=row[2],
            analysis_input_version=row[3],
            owner_id=row[4],
            owner_epoch=row[5],
            job_owner_epoch=row[6],
            owner_status=row[7],
            job_status=row[8],
            linked_analysis_input_version=row[9],
        )
