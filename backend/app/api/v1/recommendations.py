from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
    StaleInputError,
)
from app.api.pagination import validate_page_limit
from app.api.schemas.common import CursorListResponse
from app.api.schemas.recommendations import (
    RecommendationCandidateResponse,
    RecommendationRunCreateRequest,
    RecommendationRunResponse,
)
from app.api.v1.experience_common import (
    complete_idempotency,
    replay_response,
    reserve_idempotency,
)
from app.api.v1.recommendation_common import (
    recommendation_candidate_response,
    recommendation_run_response,
)
from app.api.v1.workspace_common import cursor_offset, next_cursor
from app.repo.recommendations import RecommendationRepository
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError
from app.services.recommendations import (
    RecommendationNotFoundError,
    RecommendationService,
    RecommendationStaleInputError,
    RecommendationValidationError,
)

router = APIRouter(tags=["recommendations"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post(
    "/questions/{question_id}/recommendation-runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RecommendationRunResponse,
)
def create_recommendation_run(
    question_id: UUID,
    body: RecommendationRunCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> RecommendationRunResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope=f"/api/v1/questions/{question_id}/recommendation-runs",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, RecommendationRunResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 추천 실행 응답을 확인할 수 없습니다.")
    else:
        try:
            run = RecommendationService(session).create_server_selected_recommendation_run(
                owner_user_id=principal.owner_user_id,
                question_id=question_id,
                expected_question_version=body.question_version,
                expected_snapshot_no=body.snapshot_version,
                requested_candidate_limit=body.candidate_limit,
                include_excluded=body.include_excluded,
                allow_limited_analysis=body.allow_limited_analysis,
            )
            result = _run_response(
                session,
                run_id=run.id,
                owner_user_id=principal.owner_user_id,
            )
        except RecommendationNotFoundError as error:
            raise ResourceNotFoundError() from error
        except RecommendationStaleInputError as error:
            raise StaleInputError() from error
        except RecommendationValidationError as error:
            raise InvalidInputError() from error
        complete_idempotency(
            record,
            response_status=status.HTTP_202_ACCEPTED,
            kind="recommendation_run",
            resource_id=result.id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["Location"] = f"/api/v1/recommendation-runs/{result.id}"
    return result


@router.get("/recommendation-runs/{run_id}", response_model=RecommendationRunResponse)
def get_recommendation_run(
    run_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> RecommendationRunResponse:
    return _run_response(session, run_id=run_id, owner_user_id=principal.owner_user_id)


@router.get(
    "/recommendation-runs/{run_id}/candidates",
    response_model=CursorListResponse[RecommendationCandidateResponse],
)
def list_recommendation_candidates(
    run_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query()] = 20,
) -> CursorListResponse[RecommendationCandidateResponse]:
    repository = RecommendationRepository(session)
    if repository.get_run(run_id=run_id, owner_user_id=principal.owner_user_id) is None:
        raise ResourceNotFoundError()
    page_limit = validate_page_limit(limit)
    resource = f"recommendation-candidates:{run_id}"
    offset = cursor_offset(cursor=cursor, resource=resource)
    candidates = repository.list_candidates(
        run_id=run_id,
        owner_user_id=principal.owner_user_id,
        offset=offset,
        limit=page_limit + 1,
    )
    has_next = len(candidates) > page_limit
    visible_candidates = candidates[:page_limit]
    return CursorListResponse(
        items=[recommendation_candidate_response(candidate) for candidate in visible_candidates],
        next_cursor=next_cursor(
            resource=resource,
            offset=offset,
            returned_count=len(visible_candidates),
            has_next=has_next,
        ),
    )


@router.get(
    "/recommendation-candidates/{candidate_id}",
    response_model=RecommendationCandidateResponse,
)
def get_recommendation_candidate(
    candidate_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> RecommendationCandidateResponse:
    candidate = RecommendationRepository(session).get_candidate(
        candidate_id=candidate_id,
        owner_user_id=principal.owner_user_id,
    )
    if candidate is None:
        raise ResourceNotFoundError()
    return recommendation_candidate_response(candidate)


def _run_response(
    session: Session, *, run_id: UUID, owner_user_id: UUID
) -> RecommendationRunResponse:
    repository = RecommendationRepository(session)
    run = repository.get_run(run_id=run_id, owner_user_id=owner_user_id)
    if run is None:
        raise ResourceNotFoundError()
    snapshot = repository.get_snapshot(snapshot_id=run.snapshot_id, owner_user_id=owner_user_id)
    question_version = repository.get_question_version(
        question_version_id=run.question_version_id,
        owner_user_id=owner_user_id,
    )
    if snapshot is None or question_version is None:
        raise ResourceNotFoundError()
    return recommendation_run_response(
        run,
        snapshot=snapshot,
        question_version_no=question_version.version_no,
    )


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error
