from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ActionNotAllowedError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
    StaleJobActionError,
)
from app.api.idempotency import canonical_request_hash, require_idempotency_key
from app.api.pagination import validate_page_limit
from app.api.schemas.common import CursorListResponse
from app.api.schemas.jobs import (
    JobActionRequest,
    JobCancelRequest,
    JobCheckpointResponse,
    JobListItemResponse,
    JobResponse,
    JobRetryRequest,
)
from app.api.v1.experience_common import replay_response
from app.api.v1.job_common import checkpoint_response, job_list_item, job_response
from app.api.v1.workspace_common import cursor_offset, next_cursor
from app.repo.jobs import JobRepository
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError
from app.services.idempotency import IdempotencyService
from app.services.job_actions import JobActionAcceptance, JobActionService
from app.services.jobs import (
    JobActionStaleError,
    JobIdempotencyReplayIncompleteError,
    JobNotFoundError,
    JobTransitionError,
)

router = APIRouter(tags=["jobs"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.get("/jobs", response_model=CursorListResponse[JobListItemResponse])
def list_jobs(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query()] = 20,
) -> CursorListResponse[JobListItemResponse]:
    page_limit = validate_page_limit(limit)
    resource = "jobs"
    offset = cursor_offset(cursor=cursor, resource=resource)
    repository = JobRepository(session)
    jobs = repository.list_jobs(
        owner_user_id=principal.owner_user_id,
        offset=offset,
        limit=page_limit + 1,
    )
    has_next = len(jobs) > page_limit
    visible_jobs = jobs[:page_limit]
    return CursorListResponse(
        items=[job_list_item(repository=repository, job=job) for job in visible_jobs],
        next_cursor=next_cursor(
            resource=resource,
            offset=offset,
            returned_count=len(visible_jobs),
            has_next=has_next,
        ),
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> JobResponse:
    repository = JobRepository(session)
    job = repository.get_job(job_id=job_id, owner_user_id=principal.owner_user_id)
    if job is None:
        raise ResourceNotFoundError()
    return job_response(session=session, repository=repository, job=job)


@router.get("/jobs/{job_id}/checkpoint", response_model=JobCheckpointResponse)
def get_job_checkpoint(
    job_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> JobCheckpointResponse:
    repository = JobRepository(session)
    job = repository.get_job(job_id=job_id, owner_user_id=principal.owner_user_id)
    if job is None:
        raise ResourceNotFoundError()
    return checkpoint_response(repository=repository, job=job)


@router.post(
    "/jobs/{job_id}/actions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
)
def submit_job_action(
    job_id: UUID,
    body: JobActionRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> JobResponse:
    acceptance = _submit_action(
        session=session,
        owner_user_id=principal.owner_user_id,
        job_id=job_id,
        required_action_id=body.required_action_id,
        action_code=body.action,
        expected_input_version=body.expected_input_version,
        expected_result_version=body.expected_result_version,
        acknowledge_rate_limit=body.acknowledge_rate_limit,
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
        path_scope=f"/api/v1/jobs/{job_id}/actions",
    )
    return _action_response(
        session=session,
        acceptance=acceptance,
        response=response,
    )


@router.post(
    "/jobs/{job_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
)
def retry_job(
    job_id: UUID,
    body: JobRetryRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> JobResponse:
    try:
        acceptance = JobActionService(session).retry_from_checkpoint(
            owner_user_id=principal.owner_user_id,
            job_id=job_id,
            required_action_id=body.required_action_id,
            from_stage=body.from_stage,
            expected_input_version=body.expected_input_version,
            expected_result_version=body.expected_result_version,
            acknowledge_rate_limit=body.acknowledge_rate_limit,
            idempotency_key=require_idempotency_key(idempotency_key),
            request_hash=canonical_request_hash(body.model_dump(mode="json")),
            path_scope=f"/api/v1/jobs/{job_id}/retry",
        )
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error
    except JobNotFoundError as error:
        raise ResourceNotFoundError() from error
    except JobActionStaleError as error:
        raise _stale_job_action_error() from error
    except JobTransitionError as error:
        raise ActionNotAllowedError() from error
    except JobIdempotencyReplayIncompleteError as error:
        raise InvalidInputError(message_ko="이전 Job 요청 응답을 확인할 수 없습니다.") from error
    return _action_response(session=session, acceptance=acceptance, response=response)


@router.post(
    "/jobs/{job_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobResponse,
)
def cancel_job(
    job_id: UUID,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    body: JobCancelRequest | None = None,
    idempotency_key: IdempotencyKey = None,
) -> JobResponse:
    request_body = {} if body is None else body.model_dump(mode="json")
    try:
        acceptance = JobActionService(session).request_cancellation(
            owner_user_id=principal.owner_user_id,
            job_id=job_id,
            idempotency_key=require_idempotency_key(idempotency_key),
            request_hash=canonical_request_hash(request_body),
            path_scope=f"/api/v1/jobs/{job_id}/cancel",
        )
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error
    except JobNotFoundError as error:
        raise ResourceNotFoundError() from error
    except JobTransitionError as error:
        raise ActionNotAllowedError() from error
    except JobIdempotencyReplayIncompleteError as error:
        raise InvalidInputError(message_ko="이전 Job 요청 응답을 확인할 수 없습니다.") from error
    return _action_response(session=session, acceptance=acceptance, response=response)


def _submit_action(
    *,
    session: Session,
    owner_user_id: UUID,
    job_id: UUID,
    required_action_id: UUID,
    action_code: str,
    expected_input_version: str | None,
    expected_result_version: str | None,
    acknowledge_rate_limit: bool,
    idempotency_key: str | None,
    request_body: dict[str, object],
    path_scope: str,
) -> JobActionAcceptance:
    try:
        return JobActionService(session).submit(
            owner_user_id=owner_user_id,
            job_id=job_id,
            required_action_id=required_action_id,
            action_code=action_code,
            expected_input_version=expected_input_version,
            expected_result_version=expected_result_version,
            acknowledge_rate_limit=acknowledge_rate_limit,
            idempotency_key=require_idempotency_key(idempotency_key),
            request_hash=canonical_request_hash(request_body),
            path_scope=path_scope,
        )
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error
    except JobNotFoundError as error:
        raise ResourceNotFoundError() from error
    except JobActionStaleError as error:
        raise _stale_job_action_error() from error
    except JobTransitionError as error:
        raise ActionNotAllowedError() from error
    except JobIdempotencyReplayIncompleteError as error:
        raise InvalidInputError(message_ko="이전 Job 요청 응답을 확인할 수 없습니다.") from error


def _action_response(
    *, session: Session, acceptance: JobActionAcceptance, response: Response
) -> JobResponse:
    if acceptance.replayed:
        replayed = replay_response(acceptance.idempotency_record, JobResponse)
        if replayed is not None:
            result = replayed
        else:
            result = _job_response(
                session=session,
                job_id=acceptance.job.id,
                owner_user_id=acceptance.job.owner_user_id,
            )
    else:
        result = _job_response(
            session=session,
            job_id=acceptance.job.id,
            owner_user_id=acceptance.job.owner_user_id,
        )
        IdempotencyService.complete(
            acceptance.idempotency_record,
            response_status=acceptance.response_status,
            response_ref=f"job:{result.id}",
            response_body=result.model_dump(mode="json"),
        )
    response.status_code = acceptance.response_status
    response.headers["Location"] = f"/api/v1/jobs/{result.id}"
    return result


def _job_response(*, session: Session, job_id: UUID, owner_user_id: UUID) -> JobResponse:
    repository = JobRepository(session)
    job = repository.get_job(job_id=job_id, owner_user_id=owner_user_id)
    if job is None:
        raise ResourceNotFoundError()
    return job_response(session=session, repository=repository, job=job)


def _stale_job_action_error() -> StaleJobActionError:
    return StaleJobActionError()
