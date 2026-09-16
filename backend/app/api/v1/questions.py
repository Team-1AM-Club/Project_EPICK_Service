from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ActiveReferenceExistsError,
    ApiFieldError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
    VersionConflictApiError,
)
from app.api.idempotency import parse_if_match
from app.api.schemas.questions import (
    QuestionCreateRequest,
    QuestionListItemResponse,
    QuestionMutationResponse,
    QuestionResponse,
    QuestionUpdateRequest,
    QuestionVersionSummaryResponse,
)
from app.api.v1.experience_common import (
    complete_idempotency,
    replay_resource_id,
    replay_response,
    reserve_idempotency,
)
from app.api.v1.workspace_common import (
    question_list_item,
    question_response,
    question_version_summary,
)
from app.repo.application_workspace import ApplicationWorkspaceRepository
from app.services.application_workspace import (
    UNSET,
    ActiveQuestionReferenceError,
    ApplicationWorkspaceError,
    ApplicationWorkspaceNotFoundError,
    ApplicationWorkspaceService,
    QuestionOrderConflictError,
    WorkspaceVersionConflictError,
)
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError

router = APIRouter(tags=["project-questions"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get(
    "/application-projects/{project_id}/questions",
    response_model=list[QuestionListItemResponse],
)
def list_questions(
    project_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> list[QuestionListItemResponse]:
    repository = ApplicationWorkspaceRepository(session)
    if repository.get_project(project_id=project_id, owner_user_id=principal.owner_user_id) is None:
        raise ResourceNotFoundError()
    return [
        question_list_item(question, version)
        for question, version in repository.list_questions(
            project_id=project_id, owner_user_id=principal.owner_user_id
        )
    ]


@router.post(
    "/application-projects/{project_id}/questions",
    status_code=status.HTTP_201_CREATED,
    response_model=QuestionResponse,
)
def create_question(
    project_id: UUID,
    body: QuestionCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> QuestionResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope=f"/api/v1/application-projects/{project_id}/questions",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, QuestionResponse)
        if result is None:
            result = _question_response(
                session,
                question_id=replay_resource_id(record, expected_kind="question"),
                owner_user_id=principal.owner_user_id,
            )
    else:
        try:
            question = ApplicationWorkspaceService(session).create_question(
                owner_user_id=principal.owner_user_id,
                project_id=project_id,
                display_order=body.display_order,
                prompt=body.prompt,
                source=body.source,
                character_limit=body.character_limit,
            )
        except ApplicationWorkspaceNotFoundError as error:
            raise ResourceNotFoundError() from error
        except QuestionOrderConflictError as error:
            raise InvalidInputError(
                fields=(ApiFieldError(field="display_order", reason="ALREADY_IN_USE"),)
            ) from error
        except ApplicationWorkspaceError as error:
            raise InvalidInputError() from error
        result = _question_response(
            session, question_id=question.id, owner_user_id=principal.owner_user_id
        )
        complete_idempotency(
            record,
            response_status=201,
            kind="question",
            resource_id=question.id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["Location"] = f"/api/v1/questions/{result.id}"
    response.headers["ETag"] = _etag(result.current_version)
    return result


@router.get("/questions/{question_id}", response_model=QuestionResponse)
def get_question(
    question_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    response: Response,
) -> QuestionResponse:
    result = _question_response(
        session, question_id=question_id, owner_user_id=principal.owner_user_id
    )
    response.headers["ETag"] = _etag(result.current_version)
    return result


@router.patch("/questions/{question_id}", response_model=QuestionMutationResponse)
def update_question(
    question_id: UUID,
    body: QuestionUpdateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> QuestionMutationResponse:
    expected_version = parse_if_match(if_match)
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope=f"/api/v1/questions/{question_id}",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json", exclude_unset=True),
    )
    changed_fields = sorted(body.model_fields_set)
    if replayed:
        result = replay_response(record, QuestionMutationResponse)
        if result is None:
            if replay_resource_id(record, expected_kind="question") != question_id:
                raise ResourceNotFoundError()
            result = _question_mutation_response(
                session,
                question_id=question_id,
                owner_user_id=principal.owner_user_id,
                changed_fields=changed_fields,
            )
    else:
        try:
            ApplicationWorkspaceService(session).append_question_version(
                owner_user_id=principal.owner_user_id,
                question_id=question_id,
                expected_lock_version=expected_version,
                prompt=body.prompt if "prompt" in body.model_fields_set else UNSET,
                source=body.source if "source" in body.model_fields_set else UNSET,
                character_limit=body.character_limit
                if "character_limit" in body.model_fields_set
                else UNSET,
            )
        except ApplicationWorkspaceNotFoundError as error:
            raise ResourceNotFoundError() from error
        except WorkspaceVersionConflictError as error:
            _raise_question_version_conflict(
                session,
                question_id=question_id,
                owner_user_id=principal.owner_user_id,
                expected_version=expected_version,
                source_error=error,
            )
        except ApplicationWorkspaceError as error:
            raise InvalidInputError() from error
        result = _question_mutation_response(
            session,
            question_id=question_id,
            owner_user_id=principal.owner_user_id,
            changed_fields=changed_fields,
        )
        complete_idempotency(
            record,
            response_status=200,
            kind="question",
            resource_id=question_id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["ETag"] = _etag(result.current_version)
    return result


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_question(
    question_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> Response:
    expected_version = parse_if_match(if_match)
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="DELETE",
        path_scope=f"/api/v1/questions/{question_id}",
        idempotency_key=idempotency_key,
        request_body={},
    )
    if replayed:
        if replay_resource_id(record, expected_kind="question") != question_id:
            raise ResourceNotFoundError()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        ApplicationWorkspaceService(session).archive_question(
            owner_user_id=principal.owner_user_id,
            question_id=question_id,
            expected_lock_version=expected_version,
        )
    except ApplicationWorkspaceNotFoundError as error:
        raise ResourceNotFoundError() from error
    except WorkspaceVersionConflictError as error:
        _raise_question_version_conflict(
            session,
            question_id=question_id,
            owner_user_id=principal.owner_user_id,
            expected_version=expected_version,
            source_error=error,
        )
    except ActiveQuestionReferenceError as error:
        raise ActiveReferenceExistsError() from error
    complete_idempotency(
        record,
        response_status=204,
        kind="question",
        resource_id=question_id,
        response_body={},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/questions/{question_id}/versions",
    response_model=list[QuestionVersionSummaryResponse],
)
def list_question_versions(
    question_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> list[QuestionVersionSummaryResponse]:
    repository = ApplicationWorkspaceRepository(session)
    question = repository.get_question(
        question_id=question_id, owner_user_id=principal.owner_user_id
    )
    if question is None:
        raise ResourceNotFoundError()
    return [
        question_version_summary(version, is_current=version.id == question.current_version_id)
        for version in repository.list_question_versions(
            question_id=question_id, owner_user_id=principal.owner_user_id
        )
    ]


def _question_response(
    session: Session, *, question_id: UUID, owner_user_id: UUID
) -> QuestionResponse:
    repository = ApplicationWorkspaceRepository(session)
    question = repository.get_question(question_id=question_id, owner_user_id=owner_user_id)
    if question is None:
        raise ResourceNotFoundError()
    version = repository.get_current_question_version(
        question=question, owner_user_id=owner_user_id
    )
    if version is None:
        raise ResourceNotFoundError()
    return question_response(question, version)


def _question_mutation_response(
    session: Session,
    *,
    question_id: UUID,
    owner_user_id: UUID,
    changed_fields: list[str],
) -> QuestionMutationResponse:
    return QuestionMutationResponse(
        **_question_response(
            session, question_id=question_id, owner_user_id=owner_user_id
        ).model_dump(),
        changed_fields=changed_fields,
    )


def _raise_question_version_conflict(
    session: Session,
    *,
    question_id: UUID,
    owner_user_id: UUID,
    expected_version: int,
    source_error: Exception,
) -> None:
    question = ApplicationWorkspaceRepository(session).get_question(
        question_id=question_id, owner_user_id=owner_user_id
    )
    if question is None:
        raise ResourceNotFoundError() from source_error
    raise VersionConflictApiError(
        expected_version=expected_version, actual_version=question.lock_version
    ) from source_error


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error


def _etag(version: int) -> str:
    return f'"{version}"'
