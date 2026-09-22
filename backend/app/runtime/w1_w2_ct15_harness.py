"""W1-owned synthetic state transitions used by the joint W1/W2 CT15 run.

This module deliberately exposes no generic SQL or arbitrary identifiers.  A
CT15 operator chooses a synthetic run id and one allow-listed action; W2 uses
the returned synthetic command binding but never receives a W1 database login.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.application_workspace import Company

# The deletion service creates private outbox records that reference these
# tables.  Register the models before a standalone harness session flushes.
from app.models.deletion import DeletionRequest  # noqa: F401
from app.models.identity import User
from app.models.jobs import (
    Job,
    JobCommand,
    JobExecutionLease,
    OutboxMessage,
    OwnerExecutionSlot,
)
from app.models.lifecycle_operations import JobCheckpoint
from app.models.sources import JobSourceLink, Source
from app.models.w2_commit_operations import W2CommitOperation, W2StagedResult
from app.services.deletion import DeletionOrchestrationService
from app.services.jobs import JobService
from app.services.w2_commit_gate import W2CommitGateService

_RUN_ID_PATTERN = re.compile(r"^ct15-[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
_PRIMARY_OWNER = "primary"
_SECONDARY_OWNER = "secondary"
_CONFLICT_OWNER = "conflict"
_W2_COMMAND_TYPE = "W2_SOURCE_COLLECTION"
_ANALYSIS_INPUT_VERSION = "ct15-input-v1"


class Ct15HarnessError(RuntimeError):
    """A bounded failure emitted by the synthetic-only CT15 harness."""


@dataclass(frozen=True, slots=True)
class Ct15CommandBinding:
    owner_id: UUID
    job_id: UUID
    command_id: UUID
    lease_id: UUID
    source_id: UUID
    company_id: UUID
    source_link_id: UUID
    execution_fence: int
    owner_deletion_epoch: int

    def as_safe_dict(self) -> dict[str, object]:
        """Return only synthetic IDs W2 needs to construct a pinned fixture."""

        command = {
            "schema_version": "w2.collection.v1",
            "command_id": str(self.command_id),
            "job_id": str(self.job_id),
            "authenticated_owner_ref": str(self.owner_id),
            "project_ref": None,
            "company_id": str(self.company_id),
            "source_id": str(self.source_id),
            "input_version": 1,
            "execution_fence": str(self.execution_fence),
            "purpose_ref": str(self.source_link_id),
            "core_source_decision": {
                "is_core": True,
                "decided_by": "W3",
                "rationale": "CT15_SYNTHETIC",
                "decision_revision": 1,
                "analysis_input_version": 1,
            },
            "resume_stage": "policy",
            "policy_revision": None,
            "owner_deletion_epoch": self.owner_deletion_epoch,
        }
        return {
            "owner_id": str(self.owner_id),
            "job_id": str(self.job_id),
            "command_id": str(self.command_id),
            "lease_id": str(self.lease_id),
            "source_id": str(self.source_id),
            "execution_fence": self.execution_fence,
            "owner_deletion_epoch": self.owner_deletion_epoch,
            "w2_collection_command": command,
        }


@dataclass(frozen=True, slots=True)
class Ct15Fixture:
    run_id: str
    primary: Ct15CommandBinding
    secondary: Ct15CommandBinding
    conflict: Ct15CommandBinding

    def as_safe_dict(self) -> dict[str, object]:
        if len(
            {
                self.primary.source_id,
                self.secondary.source_id,
                self.conflict.source_id,
            }
        ) != 1:
            raise Ct15HarnessError("CT15 fixture must retain one shared public Source")
        return {
            "status": "ok",
            "run_id": self.run_id,
            "fixture": "three_owners_one_shared_source",
            "primary": self.primary.as_safe_dict(),
            "secondary": self.secondary.as_safe_dict(),
            "conflict": self.conflict.as_safe_dict(),
        }


def validate_run_id(run_id: str) -> str:
    """Reject a value that could identify a non-synthetic or unbounded run."""

    normalized = run_id.strip()
    if _RUN_ID_PATTERN.fullmatch(normalized) is None:
        raise Ct15HarnessError("W1_CT15_RUN_ID must be a lowercase ct15- synthetic run id")
    return normalized


def seed_fixture(*, session: Session, run_id: str) -> Ct15Fixture:
    """Create three live synthetic bindings that share one public Source.

    Primary covers FINALIZE/PURGE, secondary covers cancel/ABORT, and conflict
    is isolated for the same-ID/different-digest retry/DLQ case.  Keeping the
    destructive conflict case on its own command permits CT15-01~09 to retain
    one run identifier without corrupting either successful terminal path.
    """

    run_id = validate_run_id(run_id)
    labels = _owner_labels(run_id)
    existing = list(
        session.scalars(select(User).where(User.display_name.in_(tuple(labels.values()))))
    )
    if existing:
        raise Ct15HarnessError("CT15 run id already has a synthetic fixture")

    company = Company(
        legal_name=f"CT15 Synthetic {run_id}",
        display_name=f"CT15 Synthetic {run_id}",
    )
    session.add(company)
    session.flush()
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url=f"https://ct15.invalid/{run_id}",
        canonical_url_hash=f"ct15-{run_id}",
        url_normalization_version="ct15-v1",
        policy_version="ct15-synthetic",
        policy_checked_at=datetime.now(UTC),
    )
    session.add(source)
    session.flush()

    bindings = {
        owner_key: _create_live_binding(
            session=session,
            owner_label=owner_label,
            run_id=run_id,
            source=source,
            company=company,
        )
        for owner_key, owner_label in labels.items()
    }
    session.flush()
    return Ct15Fixture(
        run_id=run_id,
        primary=bindings[_PRIMARY_OWNER],
        secondary=bindings[_SECONDARY_OWNER],
        conflict=bindings[_CONFLICT_OWNER],
    )


def inspect_fixture(*, session: Session, run_id: str) -> dict[str, object]:
    fixture = _load_fixture(session=session, run_id=run_id)
    return {
        **fixture.as_safe_dict(),
        "action": "inspect",
        # This is intentionally count-only.  It permits a joint CT15 assertion
        # against W2's owner/command inspection without exposing staged payloads,
        # queue addresses, receipt handles, or database internals.
        "w1_counts": _fixture_counts(
            session=session,
            primary=fixture.primary,
            secondary=fixture.secondary,
            conflict=fixture.conflict,
        ),
    }


def cancel_primary(*, session: Session, run_id: str) -> dict[str, object]:
    fixture = _load_fixture(session=session, run_id=run_id)
    job = JobService(session).request_cancellation(
        owner_user_id=fixture.primary.owner_id,
        job_id=fixture.primary.job_id,
    )
    return {
        "status": "ok",
        "run_id": fixture.run_id,
        "action": "cancel_primary",
        "job_status": job.status,
        "execution_fence": job.execution_fence,
    }


def delete_primary(*, session: Session, run_id: str) -> dict[str, object]:
    fixture = _load_fixture(session=session, run_id=run_id)
    preview_token = f"ct15-{fixture.run_id}-delete"
    deletion = DeletionOrchestrationService(session)
    preview = deletion.create_account_deletion_preview(
        owner_user_id=fixture.primary.owner_id,
        preview_token=preview_token,
    )
    request = deletion.confirm_and_start_account_deletion(
        owner_user_id=fixture.primary.owner_id,
        deletion_request_id=preview.request.id,
        preview_token=preview_token,
    )
    primary_owner = session.get(User, fixture.primary.owner_id)
    secondary_owner = session.get(User, fixture.secondary.owner_id)
    conflict_owner = session.get(User, fixture.conflict.owner_id)
    source = session.get(Source, fixture.primary.source_id)
    if (
        primary_owner is None
        or secondary_owner is None
        or conflict_owner is None
        or source is None
        or secondary_owner.account_status != "ACTIVE"
        or conflict_owner.account_status != "ACTIVE"
    ):
        raise Ct15HarnessError("CT15 deletion fixture no longer preserves owner/source isolation")
    return {
        "status": "ok",
        "run_id": fixture.run_id,
        "action": "delete_primary",
        "deletion_epoch": primary_owner.deletion_epoch,
        "deletion_request_id": str(request.id),
        "shared_source_retained": True,
        "secondary_owner_active": True,
        "conflict_owner_active": True,
    }


def redrive_primary_gate_operation(*, session: Session, run_id: str) -> dict[str, object]:
    fixture = _load_fixture(session=session, run_id=run_id)
    operation = session.scalar(
        select(W2CommitOperation)
        .where(W2CommitOperation.command_id == fixture.primary.command_id)
        .with_for_update()
    )
    if operation is None:
        raise Ct15HarnessError("CT15 primary operation is not available for recovery")
    recovery = W2CommitGateService(session).recover_operation(operation_id=operation.id)
    return {
        "status": "ok",
        "run_id": fixture.run_id,
        "action": "redrive_primary_gate_operation",
        "operation_state": recovery.operation.state,
        "operation_revision": recovery.operation.operation_revision,
        "requeued": recovery.requeued,
        "message_id": str(recovery.outbox.id) if recovery.outbox is not None else None,
    }


def _fixture_counts(
    *,
    session: Session,
    primary: Ct15CommandBinding,
    secondary: Ct15CommandBinding,
    conflict: Ct15CommandBinding,
) -> dict[str, object]:
    """Return comparable W1 aggregates for the two allow-listed CT15 commands."""

    return {
        _PRIMARY_OWNER: _command_counts(session=session, command_ids=(primary.command_id,)),
        _SECONDARY_OWNER: _command_counts(session=session, command_ids=(secondary.command_id,)),
        _CONFLICT_OWNER: _command_counts(session=session, command_ids=(conflict.command_id,)),
        "total": _command_counts(
            session=session,
            command_ids=(
                primary.command_id,
                secondary.command_id,
                conflict.command_id,
            ),
        ),
    }


def _command_counts(*, session: Session, command_ids: tuple[UUID, ...]) -> dict[str, object]:
    """Aggregate only the W1 rows bound to the supplied synthetic commands."""

    command_filter = JobCommand.id.in_(command_ids)
    operation_filter = W2CommitOperation.command_id.in_(command_ids)
    return {
        "operations_by_state": _count_by_label(
            session=session,
            label=W2CommitOperation.state,
            where=operation_filter,
        ),
        "staged_results_by_state": _count_by_label(
            session=session,
            label=W2StagedResult.payload_state,
            where=W2StagedResult.command_id.in_(command_ids),
        ),
        # W1 has no separate collection-result table.  A result effect becomes
        # durable when this child command is consumed by the locked finalizer.
        "result_effects": _count_rows(
            session=session,
            model=JobCommand,
            where=command_filter & (JobCommand.status == "CONSUMED"),
        ),
        "checkpoints": _count_rows(
            session=session,
            model=JobCheckpoint,
            where=JobCheckpoint.job_id.in_(
                select(JobCommand.job_id).where(command_filter)
            ),
        ),
        "commit_gate_outbox_by_action_and_status": _count_commit_gate_outbox(
            session=session,
            command_ids=command_ids,
        ),
    }


def _count_by_label(*, session: Session, label: object, where: object) -> dict[str, int]:
    rows = session.execute(
        select(label, func.count()).where(where).group_by(label).order_by(label)
    ).all()
    return {str(value): int(count) for value, count in rows}


def _count_rows(*, session: Session, model: object, where: object) -> int:
    return int(session.scalar(select(func.count()).select_from(model).where(where)) or 0)


def _count_commit_gate_outbox(
    *, session: Session, command_ids: tuple[UUID, ...]
) -> dict[str, int]:
    action = OutboxMessage.payload["action"].astext
    rows = session.execute(
        select(action, OutboxMessage.status, func.count())
        .where(
            OutboxMessage.command_id.in_(command_ids),
            OutboxMessage.message_type == "w1.private.w2.commit-gate.v1",
        )
        .group_by(action, OutboxMessage.status)
        .order_by(action, OutboxMessage.status)
    ).all()
    return {f"{value}:{status}": int(count) for value, status, count in rows}


def _owner_labels(run_id: str) -> dict[str, str]:
    return {
        _PRIMARY_OWNER: f"CT15 synthetic {run_id} primary",
        _SECONDARY_OWNER: f"CT15 synthetic {run_id} secondary",
        _CONFLICT_OWNER: f"CT15 synthetic {run_id} conflict",
    }


def _create_live_binding(
    *,
    session: Session,
    owner_label: str,
    run_id: str,
    source: Source,
    company: Company,
) -> Ct15CommandBinding:
    owner_id = uuid4()
    job_id = uuid4()
    command_id = uuid4()
    lease_id = uuid4()
    source_link_id = uuid4()
    owner = User(
        id=owner_id,
        display_name=owner_label,
        locale="ko-KR",
        timezone="Asia/Seoul",
    )
    job = Job(
        id=job_id,
        owner_user_id=owner_id,
        job_type="SOURCE_COLLECTION",
        status="RUNNING",
        dispatch_status="CLAIMED",
        active_lease_id=lease_id,
        owner_deletion_epoch=0,
        analysis_input_version=_ANALYSIS_INPUT_VERSION,
    )
    lease = JobExecutionLease(
        id=lease_id,
        job_id=job_id,
        owner_user_id=owner_id,
        slot_no=1,
        execution_fence=1,
        owner_deletion_epoch=0,
        worker_ref=f"ct15:{run_id}",
        claimed_at=datetime.now(UTC),
    )
    slot = OwnerExecutionSlot(
        owner_user_id=owner_id,
        slot_no=1,
        job_id=job_id,
        lease_id=lease_id,
        claimed_at=datetime.now(UTC),
    )
    command = JobCommand(
        id=command_id,
        job_id=job_id,
        owner_user_id=owner_id,
        command_type=_W2_COMMAND_TYPE,
        command_schema_version="1.0",
        command_sequence=1,
        execution_fence=1,
        owner_deletion_epoch=0,
        analysis_input_version=_ANALYSIS_INPUT_VERSION,
        status="ENQUEUED",
        payload={
            "command_type": _W2_COMMAND_TYPE,
            "w2_command": {
                "schema_version": "w2.collection.v1",
                "command_id": str(command_id),
                "job_id": str(job_id),
                "authenticated_owner_ref": str(owner_id),
                "project_ref": None,
                "company_id": str(company.id),
                "source_id": str(source.id),
                "input_version": 1,
                "execution_fence": "1",
                "purpose_ref": str(source_link_id),
                "core_source_decision": {
                    "is_core": True,
                    "decided_by": "W3",
                    "rationale": "CT15_SYNTHETIC",
                    "decision_revision": 1,
                    "analysis_input_version": 1,
                },
                "resume_stage": "policy",
                "policy_revision": None,
                "owner_deletion_epoch": 0,
            },
            "runtime": {"lease_id": str(lease_id), "input_version": 1},
        },
    )
    source_link = JobSourceLink(
        id=source_link_id,
        job_id=job_id,
        owner_user_id=owner_id,
        source_id=source.id,
        source_version_id=None,
        command_id=command_id,
        purpose_ref="CT15_GATE",
        analysis_input_version=_ANALYSIS_INPUT_VERSION,
    )
    # ``job_execution_leases`` has an immediate FK to the owner's slot.  Flush
    # the durable binding in that exact order rather than relying on SQLAlchemy
    # to infer it from unrelated model relationships.
    session.add_all((owner, job, command, source_link))
    session.flush()
    session.add(slot)
    session.flush()
    session.add(lease)
    session.flush()
    return Ct15CommandBinding(
        owner_id=owner_id,
        job_id=job_id,
        command_id=command_id,
        lease_id=lease_id,
        source_id=source.id,
        company_id=company.id,
        source_link_id=source_link_id,
        execution_fence=1,
        owner_deletion_epoch=0,
    )


def _load_fixture(*, session: Session, run_id: str) -> Ct15Fixture:
    run_id = validate_run_id(run_id)
    labels = _owner_labels(run_id)
    owners = {
        owner.display_name: owner
        for owner in session.scalars(
            select(User).where(User.display_name.in_(tuple(labels.values())))
        )
    }
    if set(owners) != set(labels.values()):
        raise Ct15HarnessError("CT15 synthetic fixture was not found for this run id")
    bindings = {
        owner_key: _load_binding(session=session, owner=owners[owner_label])
        for owner_key, owner_label in labels.items()
    }
    fixture = Ct15Fixture(
        run_id=run_id,
        primary=bindings[_PRIMARY_OWNER],
        secondary=bindings[_SECONDARY_OWNER],
        conflict=bindings[_CONFLICT_OWNER],
    )
    if len(
        {
            fixture.primary.source_id,
            fixture.secondary.source_id,
            fixture.conflict.source_id,
        }
    ) != 1:
        raise Ct15HarnessError("CT15 synthetic fixture lost shared Source isolation")
    return fixture


def _load_binding(*, session: Session, owner: User) -> Ct15CommandBinding:
    job = session.scalar(
        select(Job)
        .where(Job.owner_user_id == owner.id, Job.job_type == "SOURCE_COLLECTION")
        .order_by(Job.created_at, Job.id)
        .limit(1)
    )
    if job is None:
        raise Ct15HarnessError("CT15 fixture Job was not found")
    command = session.scalar(
        select(JobCommand)
        .where(
            JobCommand.job_id == job.id,
            JobCommand.owner_user_id == owner.id,
            JobCommand.command_type == _W2_COMMAND_TYPE,
        )
        .order_by(JobCommand.command_sequence, JobCommand.id)
        .limit(1)
    )
    lease = session.scalar(
        select(JobExecutionLease)
        .where(
            JobExecutionLease.job_id == job.id,
            JobExecutionLease.owner_user_id == owner.id,
        )
        .order_by(JobExecutionLease.claimed_at.desc(), JobExecutionLease.id.desc())
        .limit(1)
    )
    if command is None or lease is None:
        raise Ct15HarnessError("CT15 fixture is missing its W2 command binding")
    source_link = session.scalar(
        select(JobSourceLink)
        .where(
            JobSourceLink.command_id == command.id,
            JobSourceLink.job_id == job.id,
            JobSourceLink.owner_user_id == owner.id,
        )
        .limit(1)
    )
    if source_link is None:
        raise Ct15HarnessError("CT15 fixture is missing its Source binding")
    source = session.get(Source, source_link.source_id)
    if source is None:
        raise Ct15HarnessError("CT15 fixture Source was not found")
    return Ct15CommandBinding(
        owner_id=owner.id,
        job_id=job.id,
        command_id=command.id,
        lease_id=lease.id,
        source_id=source.id,
        company_id=source.company_id,
        source_link_id=source_link.id,
        execution_fence=command.execution_fence,
        owner_deletion_epoch=command.owner_deletion_epoch,
    )
