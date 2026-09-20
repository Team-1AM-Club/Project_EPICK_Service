from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.repo.w3_authority import W3AuthorityRepository

INACTIVE_JOB_STATUSES = frozenset({"CANCEL_REQUESTED", "CANCELLED", "FAILED_FINAL"})


class W3AuthorityError(RuntimeError):
    pass


class W3AuthorityNotFoundError(W3AuthorityError):
    pass


class W3AuthorityConflictError(W3AuthorityError):
    pass


class W3DecisionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    company_id: UUID
    source_id: UUID
    analysis_input_version: Annotated[str, Field(min_length=1, max_length=64, pattern=r"\S")]


class W3Authorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    context: W3DecisionContext
    owner_id: UUID
    owner_epoch: Annotated[int, Field(strict=True, ge=0)]
    active: Annotated[bool, Field(strict=True)]


class W3AuthorityService:
    def __init__(self, session: Session) -> None:
        self.repository = W3AuthorityRepository(session)

    def current(self, *, job_id: UUID, source_id: UUID) -> W3Authorization:
        value = self.repository.get_currentness(job_id=job_id, source_id=source_id)
        if value is None:
            raise W3AuthorityNotFoundError("AUTHORITY_CONTEXT_NOT_FOUND")
        if value.analysis_input_version is None or not value.analysis_input_version.strip():
            raise W3AuthorityConflictError("AUTHORITY_INPUT_VERSION_UNAVAILABLE")

        active = (
            value.owner_status == "ACTIVE"
            and value.job_status not in INACTIVE_JOB_STATUSES
            and value.owner_epoch == value.job_owner_epoch
            and value.linked_analysis_input_version == value.analysis_input_version
        )
        return W3Authorization(
            context=W3DecisionContext(
                job_id=value.job_id,
                company_id=value.company_id,
                source_id=value.source_id,
                analysis_input_version=value.analysis_input_version,
            ),
            owner_id=value.owner_id,
            owner_epoch=value.owner_epoch,
            active=active,
        )
