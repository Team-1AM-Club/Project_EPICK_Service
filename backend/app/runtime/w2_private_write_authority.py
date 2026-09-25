"""Read-only, exact W1 authority decision for W2 private writes.

Transport authentication belongs to the protected lookup adapter. A response
from this module is not a transferable bearer credential; W2 must request a
fresh decision for each write and bind it to the command/Job/scope it applies.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import set_local_owner_context
from app.models.application_workspace import ApplicationProject
from app.models.identity import User
from app.models.jobs import Job, JobCommand
from app.models.w2_commit_operations import W2CommitOperation

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


class GateAuthorityRequest(PrivateWriteAuthorityRequest):
    """Fresh decision for one W1-issued commit-gate action or its ACK delivery."""

    schema_version: Literal["w1.private.w2-gate-authority.v1"]
    operation_id: UUID
    operation_revision: int = Field(ge=1, le=2**63 - 1)
    action: Literal["PREPARE", "FINALIZE", "ABORT", "PURGE"]
    phase: Literal["APPLY", "ACK_RELAY"]
    result_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    purge_owner_deletion_epoch: int | None = Field(default=None, ge=1, le=2**63 - 1)

    @model_validator(mode="after")
    def validate_purge_epoch(self) -> GateAuthorityRequest:
        if self.action == "PURGE":
            if (
                self.purge_owner_deletion_epoch is None
                or self.purge_owner_deletion_epoch <= self.owner_deletion_epoch
            ):
                raise ValueError("PURGE requires a newer owner deletion epoch")
        elif self.purge_owner_deletion_epoch is not None:
            raise ValueError("only PURGE permits a purge epoch")
        return self


class GateAuthorityResponse(GateAuthorityRequest):
    authority_ref: str


_GATE_PENDING_STATES = {
    "PREPARE": "PREPARE_PENDING",
    "FINALIZE": "FINALIZE_PENDING",
    "ABORT": "ABORT_PENDING",
    "PURGE": "PURGE_PENDING",
}
_GATE_APPLIED_STATES = {
    "PREPARE": "PREPARED",
    "FINALIZE": "FINALIZED",
    "ABORT": "ABORTED",
    "PURGE": "PURGED",
}


def decide_gate_authority(
    *, session: Session, request: GateAuthorityRequest
) -> GateAuthorityResponse:
    """Authorize only the exact durable W1 gate action, including terminal cleanup."""
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
    if command is None or command["owner_user_id"] != request.owner_user_id:
        raise PrivateWriteAuthorityDenied("gate command is not current")
    set_local_owner_context(session, command["owner_user_id"])
    operation = (
        session.execute(
            select(
                W2CommitOperation.id,
                W2CommitOperation.command_id,
                W2CommitOperation.job_id,
                W2CommitOperation.owner_user_id,
                W2CommitOperation.execution_fence,
                W2CommitOperation.owner_deletion_epoch,
                W2CommitOperation.purge_owner_deletion_epoch,
                W2CommitOperation.result_digest,
                W2CommitOperation.operation_revision,
                W2CommitOperation.state,
            ).where(W2CommitOperation.id == request.operation_id)
        )
        .mappings()
        .one_or_none()
    )
    job = (
        session.execute(
            select(
                Job.id,
                Job.owner_user_id,
                Job.project_id,
                Job.status,
                Job.execution_fence,
                Job.owner_deletion_epoch,
            ).where(Job.id == request.job_id)
        )
        .mappings()
        .one_or_none()
    )
    owner = (
        session.execute(
            select(User.id, User.deletion_epoch, User.account_status).where(
                User.id == request.owner_user_id
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        operation is None
        or job is None
        or owner is None
        or command["job_id"] != request.job_id
        or command["command_type"] not in _W2_COMMAND_TYPES
        or command["execution_fence"] != request.execution_fence
        or command["owner_deletion_epoch"] != request.owner_deletion_epoch
        or job["owner_user_id"] != request.owner_user_id
        or operation["command_id"] != request.command_id
        or operation["job_id"] != request.job_id
        or operation["owner_user_id"] != request.owner_user_id
        or operation["execution_fence"] != request.execution_fence
        or operation["owner_deletion_epoch"] != request.owner_deletion_epoch
        or operation["purge_owner_deletion_epoch"] != request.purge_owner_deletion_epoch
        or operation["result_digest"] != request.result_digest
    ):
        raise PrivateWriteAuthorityDenied("gate binding is not current")
    pending = (
        operation["state"] == _GATE_PENDING_STATES[request.action]
        and operation["operation_revision"] == request.operation_revision
    )
    already_applied = (
        request.phase == "ACK_RELAY"
        and operation["state"] == _GATE_APPLIED_STATES[request.action]
        and operation["operation_revision"] == request.operation_revision + 1
    )
    historically_issued = False
    if (
        request.phase == "ACK_RELAY"
        and operation["operation_revision"] > request.operation_revision
        and not (pending or already_applied)
    ):
        historically_issued = bool(
            session.scalar(
                select(
                    func.public.w2_gate_outbox_was_issued(
                        request.operation_id,
                        request.operation_revision,
                        request.action,
                        request.command_id,
                        request.job_id,
                        request.owner_user_id,
                        request.execution_fence,
                        request.owner_deletion_epoch,
                        request.result_digest,
                        request.purge_owner_deletion_epoch,
                    )
                )
            )
        )
    if not (pending or already_applied or historically_issued):
        raise PrivateWriteAuthorityDenied("gate action is no longer expected")
    if job["project_id"] is None:
        if request.scope != {"type": "ACCOUNT"}:
            raise PrivateWriteAuthorityDenied("gate account scope does not match")
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
            or request.scope != {"type": "PROJECT", "project_id": str(job["project_id"])}
        ):
            raise PrivateWriteAuthorityDenied("gate project scope does not match")
    if request.action in {"PREPARE", "FINALIZE"} and not (already_applied or historically_issued):
        if (
            owner["account_status"] != "ACTIVE"
            or owner["deletion_epoch"] != request.owner_deletion_epoch
            or command["status"] in _DISALLOWED_COMMAND_STATUSES
            or job["status"] in _DISALLOWED_JOB_STATUSES
            or job["execution_fence"] != request.execution_fence
            or job["owner_deletion_epoch"] != request.owner_deletion_epoch
            or (job["project_id"] is not None and project["status"] == "ARCHIVED")
        ):
            raise PrivateWriteAuthorityDenied("gate forward action is not current")
    elif (
        request.action == "PURGE"
        and not (already_applied or historically_issued)
        and owner["deletion_epoch"] != request.purge_owner_deletion_epoch
    ):
        raise PrivateWriteAuthorityDenied("gate purge epoch is not current")
    return GateAuthorityResponse(
        **request.model_dump(),
        authority_ref=(
            f"w1:gate:{request.operation_id}:revision:{request.operation_revision}"
            f":action:{request.action}:phase:{request.phase}"
        ),
    )


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
