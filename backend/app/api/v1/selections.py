from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ActionNotAllowedError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
    StaleInputError,
)
from app.api.schemas.selections import CandidateSelectionRequest, MaterialSelectionResponse
from app.api.v1.experience_common import (
    complete_idempotency,
    replay_resource_id,
    replay_response,
    reserve_idempotency,
)
from app.api.v1.recommendation_common import material_selection_response
from app.repo.recommendations import RecommendationRepository
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError
from app.services.recommendations import (
    MaterialSelectionConflictError,
    RecommendationNotFoundError,
    RecommendationService,
    RecommendationStaleInputError,
    RecommendationValidationError,
)

router = APIRouter(tags=["material-selections"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post(
    "/recommendation-candidates/{candidate_id}/select",
    response_model=MaterialSelectionResponse,
)
def select_recommendation_candidate(
    candidate_id: UUID,
    body: CandidateSelectionRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> MaterialSelectionResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope=f"/api/v1/recommendation-candidates/{candidate_id}/select",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, MaterialSelectionResponse)
        if result is None:
            selection_id = replay_resource_id(record, expected_kind="selection")
            result = _selection_response(
                session,
                selection_id=selection_id,
                owner_user_id=principal.owner_user_id,
            )
        response.status_code = record.response_status or status.HTTP_200_OK
    else:
        try:
            selection, created = RecommendationService(session).select_candidate(
                owner_user_id=principal.owner_user_id,
                candidate_id=candidate_id,
                question_id=body.question_id,
                run_id=body.recommendation_run_id,
                result_version=body.result_version,
                replace_existing=body.replace_existing,
            )
            result = _selection_response(
                session,
                selection_id=selection.id,
                owner_user_id=principal.owner_user_id,
            )
        except RecommendationNotFoundError as error:
            raise ResourceNotFoundError() from error
        except RecommendationStaleInputError as error:
            raise StaleInputError() from error
        except MaterialSelectionConflictError as error:
            raise ActionNotAllowedError() from error
        except RecommendationValidationError as error:
            raise InvalidInputError() from error
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        complete_idempotency(
            record,
            response_status=response.status_code,
            kind="selection",
            resource_id=result.selection_id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["Location"] = f"/api/v1/questions/{result.question_id}/selection"
    return result


@router.get(
    "/questions/{question_id}/selection",
    response_model=MaterialSelectionResponse,
)
def get_current_material_selection(
    question_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> MaterialSelectionResponse:
    selection = RecommendationRepository(session).get_current_selection(
        question_id=question_id,
        owner_user_id=principal.owner_user_id,
    )
    if selection is None:
        raise ResourceNotFoundError()
    return _selection_response(
        session,
        selection_id=selection.id,
        owner_user_id=principal.owner_user_id,
    )


@router.delete("/questions/{question_id}/selection", status_code=status.HTTP_204_NO_CONTENT)
def clear_current_material_selection(
    question_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> Response:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="DELETE",
        path_scope=f"/api/v1/questions/{question_id}/selection",
        idempotency_key=idempotency_key,
        request_body={},
    )
    if replayed:
        if replay_resource_id(record, expected_kind="selection") is None:
            raise ResourceNotFoundError()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        selection = RecommendationService(session).clear_current_material_selection(
            owner_user_id=principal.owner_user_id,
            question_id=question_id,
        )
    except RecommendationNotFoundError as error:
        raise ResourceNotFoundError() from error
    complete_idempotency(
        record,
        response_status=status.HTTP_204_NO_CONTENT,
        kind="selection",
        resource_id=selection.id,
        response_body={},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _selection_response(
    session: Session, *, selection_id: UUID, owner_user_id: UUID
) -> MaterialSelectionResponse:
    repository = RecommendationRepository(session)
    selection = repository.get_selection(
        selection_set_id=selection_id,
        owner_user_id=owner_user_id,
    )
    if selection is None:
        raise ResourceNotFoundError()
    run = repository.get_run(run_id=selection.run_id, owner_user_id=owner_user_id)
    items = repository.list_selection_items(
        selection_set_id=selection.id,
        owner_user_id=owner_user_id,
    )
    if run is None or len(items) != 1:
        raise ResourceNotFoundError()
    item = items[0]
    candidate = repository.get_candidate(
        candidate_id=item.candidate_id,
        owner_user_id=owner_user_id,
    )
    episode_versions = repository.get_episode_versions(
        episode_version_ids=(candidate.episode_version_id,) if candidate is not None else (),
        owner_user_id=owner_user_id,
    )
    if candidate is None or len(episode_versions) != 1:
        raise ResourceNotFoundError()
    return material_selection_response(
        selection,
        run=run,
        item=item,
        candidate=candidate,
        episode_version=episode_versions[0],
    )


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error
