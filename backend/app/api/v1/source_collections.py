from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ApiFieldError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
    UnsupportedSourceUrlError,
)
from app.api.idempotency import require_idempotency_key
from app.api.schemas.source_collections import (
    SourceCollectionAcceptanceResponse,
    SourceCollectionCreateRequest,
    SourceCollectionErrorResponse,
    SourceCollectionProgressResponse,
)
from app.api.v1.job_common import job_response
from app.models.jobs import Job
from app.models.sources import JobSourceLink
from app.repo.jobs import JobRepository
from app.services.idempotency import IdempotencyConflictError as ServiceIdempotencyConflictError
from app.services.source_collections import (
    SourceCollectionCompanyUnavailableError,
    SourceCollectionNotFoundError,
    SourceCollectionService,
)
from app.services.source_url_policy import SourceUrlDomainMismatchError, SourceUrlPolicyError

router = APIRouter(prefix="/application-projects", tags=["source-collections"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


@router.post(
    "/{project_id}/source-collections",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SourceCollectionAcceptanceResponse,
    responses={
        202: {"headers": {"Location": {"schema": {"type": "string"}}}},
        400: {
            "model": SourceCollectionErrorResponse,
            "description": "지원하지 않거나 안전하지 않은 공식 URL입니다.",
        },
    },
)
def create_source_collection(
    project_id: UUID,
    body: SourceCollectionCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey,
) -> SourceCollectionAcceptanceResponse:
    try:
        accepted = SourceCollectionService(session).accept(
            owner_user_id=principal.owner_user_id,
            project_id=project_id,
            official_url=body.official_url,
            purpose=body.purpose,
            idempotency_key=require_idempotency_key(idempotency_key),
        )
    except SourceCollectionNotFoundError as error:
        raise ResourceNotFoundError() from error
    except SourceCollectionCompanyUnavailableError as error:
        raise ResourceNotFoundError() from error
    except SourceUrlDomainMismatchError as error:
        raise InvalidInputError(
            fields=(ApiFieldError(field="official_url", reason="COMPANY_DOMAIN_MISMATCH"),)
        ) from error
    except SourceUrlPolicyError as error:
        raise UnsupportedSourceUrlError() from error
    except ServiceIdempotencyConflictError as error:
        raise IdempotencyConflictError() from error
    result = SourceCollectionAcceptanceResponse(
        job_id=accepted.job.id,
        source_id=accepted.source.id,
        status=accepted.job.status,
        replayed=accepted.replayed,
    )
    response.headers["Location"] = f"/api/v1/jobs/{result.job_id}"
    return result


@router.get(
    "/{project_id}/source-collections",
    response_model=SourceCollectionProgressResponse,
)
def get_latest_source_collection(
    project_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> SourceCollectionProgressResponse:
    job_id = session.scalar(
        select(Job.id)
        .where(
            Job.project_id == project_id,
            Job.owner_user_id == principal.owner_user_id,
            Job.job_type == "SOURCE_REGISTRATION",
        )
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    if job_id is None:
        raise ResourceNotFoundError()
    return _source_collection_progress(
        project_id=project_id,
        job_id=job_id,
        owner_user_id=principal.owner_user_id,
        session=session,
    )


@router.get(
    "/{project_id}/source-collections/{job_id}",
    response_model=SourceCollectionProgressResponse,
)
def get_source_collection_progress(
    project_id: UUID,
    job_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> SourceCollectionProgressResponse:
    return _source_collection_progress(
        project_id=project_id,
        job_id=job_id,
        owner_user_id=principal.owner_user_id,
        session=session,
    )


def _source_collection_progress(
    *, project_id: UUID, job_id: UUID, owner_user_id: UUID, session: Session
) -> SourceCollectionProgressResponse:
    jobs = JobRepository(session)
    job = jobs.get_job(job_id=job_id, owner_user_id=owner_user_id)
    if job is None or job.project_id != project_id or job.job_type != "SOURCE_REGISTRATION":
        raise ResourceNotFoundError()
    source_id = session.scalar(
        select(JobSourceLink.source_id).where(
            JobSourceLink.job_id == job.id,
            JobSourceLink.owner_user_id == owner_user_id,
        )
    )
    if source_id is None:
        raise ResourceNotFoundError()
    public_job = job_response(session=session, repository=jobs, job=job)
    return SourceCollectionProgressResponse(
        job_id=job.id,
        source_id=source_id,
        status=public_job.status,
        stage=public_job.stage,
        progress=public_job.progress,
    )
