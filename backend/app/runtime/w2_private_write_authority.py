"""Read-only, exact W1 authority decision for W2 private writes.

Transport authentication belongs to the protected lookup adapter. A response
from this module is not a transferable bearer credential; W2 must request a
fresh decision for each write and bind it to the command/Job/scope it applies.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import set_local_owner_context
from app.models.application_workspace import ApplicationProject
from app.models.identity import User
from app.models.jobs import Job, JobCommand

_W2_COMMAND_TYPES = frozenset({"W2_SOURCE_COLLECTION", "W2_DIRECT_SOURCE_REGISTRATION"})
_DISALLOWED_JOB_STATUSES = frozenset({"CANCEL_REQUESTED", "CANCELLED", "FAILED_FINAL"})
_DISALLOWED_COMMAND_STATUSES = frozenset({"INVALIDATED", "FAILED"})


class PrivateWriteAuthorityDenied(ValueError):
    pass


class PrivateWriteAuthorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["w1.private.w2-write-authority.v1"]
    owner_user_id: UUID
    owner_deletion_epoch: int = Field(ge=0, le=2**63 - 1)
    command_id: UUID
    job_id: UUID
    execution_fence: int = Field(ge=1, le=2**63 - 1)
    scope: dict[str, str]

    @field_validator("scope")
    @classmethod
    def require_canonical_scope(cls, value: dict[str, str]) -> dict[str, str]:
        if value == {"type": "ACCOUNT"}:
            return value
        if value.keys() != {"type", "project_id"} or value.get("type") != "PROJECT":
            raise ValueError("private write scope must be explicit")
        try:
            project_id = UUID(value["project_id"])
        except ValueError as error:
            raise ValueError("private write project ID must be canonical") from error
        if value["project_id"] != str(project_id):
            raise ValueError("private write project ID must be canonical")
        return value


class PrivateWriteAuthorityResponse(PrivateWriteAuthorityRequest):
    authority_ref: str


def decide_private_write_authority(
    *, session: Session, request: PrivateWriteAuthorityRequest
) -> PrivateWriteAuthorityResponse:
    """Deny unless current canonical W1 rows match every supplied binding."""
    command = (
        session.execute(
            select(
                JobCommand.id,
                JobCommand.job_id,
                JobCommand.owner_user_id,
                JobCommand.command_type,
                JobCommand.execution_fence,
                JobCommand.owner_deletion_epoch,
                JobCommand.status,
            ).where(JobCommand.id == request.command_id)
        )
        .mappings()
        .one_or_none()
    )
    if command is None:
        raise PrivateWriteAuthorityDenied("private write command is not current")
    # Resolve the owner from the W1 command, never from the caller-controlled body.
    set_local_owner_context(session, command["owner_user_id"])
    job = (
        session.execute(
            select(
                Job.id,
                Job.owner_user_id,
                Job.project_id,
                Job.status,
                Job.execution_fence,
                Job.owner_deletion_epoch,
            ).where(Job.id == command["job_id"])
        )
        .mappings()
        .one_or_none()
    )
    owner = (
        session.execute(
            select(User.id, User.deletion_epoch, User.account_status).where(
                User.id == command["owner_user_id"]
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        job is None
        or owner is None
        or command["id"] != request.command_id
        or command["job_id"] != request.job_id
        or command["owner_user_id"] != request.owner_user_id
        or command["command_type"] not in _W2_COMMAND_TYPES
        or command["status"] in _DISALLOWED_COMMAND_STATUSES
        or command["execution_fence"] != request.execution_fence
        or command["owner_deletion_epoch"] != request.owner_deletion_epoch
        or job["id"] != request.job_id
        or job["owner_user_id"] != request.owner_user_id
        or job["status"] in _DISALLOWED_JOB_STATUSES
        or job["execution_fence"] != request.execution_fence
        or job["owner_deletion_epoch"] != request.owner_deletion_epoch
        or owner["id"] != request.owner_user_id
        or owner["account_status"] != "ACTIVE"
        or owner["deletion_epoch"] != request.owner_deletion_epoch
    ):
        raise PrivateWriteAuthorityDenied("private write binding is not current")
    if job["project_id"] is None:
        if request.scope != {"type": "ACCOUNT"}:
            raise PrivateWriteAuthorityDenied("private write scope is not current")
    else:
        project = (
            session.execute(
                select(
                    ApplicationProject.id,
                    ApplicationProject.owner_user_id,
                    ApplicationProject.status,
                ).where(ApplicationProject.id == job["project_id"])
            )
            .mappings()
            .one_or_none()
        )
        if (
            project is None
            or project["owner_user_id"] != request.owner_user_id
            or project["status"] == "ARCHIVED"
            or request.scope != {"type": "PROJECT", "project_id": str(job["project_id"])}
        ):
            raise PrivateWriteAuthorityDenied("private write project scope is not current")
    return PrivateWriteAuthorityResponse(
        **request.model_dump(),
        authority_ref=(
            f"w1:command:{request.command_id}:fence:{request.execution_fence}"
            f":epoch:{request.owner_deletion_epoch}"
        ),
    )
