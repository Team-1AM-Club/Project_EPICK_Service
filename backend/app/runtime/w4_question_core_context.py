"""W1-owned currentness resolution for the W4 Question Core producer.

This module is intentionally a private, read-only projection.  It does not
create a W2 command, resolve a company, or replace the W1 inbound consumer's
locked commit-time validation.  Its only purpose is to let W4 fail closed
before sending a synthetic CT-12 decision.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    ProjectQuestion,
    QuestionVersion,
)
from app.models.identity import User
from app.models.jobs import Job, JobCoreDecisionBinding, JobRequiredAction
from app.models.sources import JobSourceLink, Source
from app.models.w4_question_core import W4QuestionCoreContext
from app.runtime.question_core_binding import QUESTION_MATCHING_SCOPE, W4_QUESTION_CORE_PRODUCER

W4_QUESTION_CORE_CONTEXT_SCHEMA_VERSION = "w1.private.w4-question-core-context.v1"
W4_QUESTION_CORE_CONTEXT_PRINCIPAL = "w4"
W4_QUESTION_CORE_CONTEXT_ACTION = "CORE_DECISION_REQUIRED"


class W4QuestionCoreContextError(ValueError):
    """A safe, non-sensitive reason an opaque context cannot be issued."""


@dataclass(frozen=True, slots=True)
class W4QuestionCoreResolvedContext:
    context_key: UUID
    job_id: UUID
    question_version_id: UUID
    source_id: UUID
    analysis_input_version: str
    authorization_revision: str
    current_decision_version: int
    data_kind: Literal["SYNTHETIC", "REAL"]
    processing_allowed: bool
    question_current: bool
    source_active: bool
    revoked: bool
    valid_until: datetime


def issue_w4_question_core_context(
    *,
    session: Session,
    job: Job,
    question_version_id: UUID,
    source_id: UUID,
    valid_for: timedelta,
    data_kind: Literal["SYNTHETIC", "REAL"] = "SYNTHETIC",
) -> W4QuestionCoreContext:
    """Create an opaque handle after W1 has selected a current synthetic fixture.

    REAL issuance is intentionally disabled until the separately approved P2/P3
    policy exists.  The caller owns the transaction and must hold the W1 job
    boundary appropriate to the dispatch that creates this handle.
    """

    if data_kind != "SYNTHETIC":
        raise W4QuestionCoreContextError("W4_QUESTION_CORE_REAL_CONTEXT_DISABLED")
    if valid_for <= timedelta(0):
        raise W4QuestionCoreContextError("W4_QUESTION_CORE_CONTEXT_TTL_INVALID")
    if not job.analysis_input_version:
        raise W4QuestionCoreContextError("W4_QUESTION_CORE_CONTEXT_INPUT_MISSING")

    now = datetime.now(UTC)
    context = W4QuestionCoreContext(
        job_id=job.id,
        owner_user_id=job.owner_user_id,
        question_version_id=question_version_id,
        source_id=source_id,
        analysis_input_version=job.analysis_input_version,
        execution_fence=job.execution_fence,
        owner_deletion_epoch=job.owner_deletion_epoch,
        data_kind=data_kind,
        expires_at=now + valid_for,
        created_at=now,
    )
    session.add(context)
    session.flush()
    return context


def resolve_w4_question_core_context(
    *, session: Session, context_key: UUID
) -> W4QuestionCoreResolvedContext | None:
    """Resolve an opaque context against the latest W1 state without a write lock.

    This is intentionally a pre-send check.  A concurrent cancellation, deletion,
    or revision change after this read remains safe because W1's inbound consumer
    repeats its full relation validation in its own locked transaction.
    """

    context = session.get(W4QuestionCoreContext, context_key)
    if context is None:
        return None

    # The W4 context login has deliberately column-limited grants.  Do not use
    # ``session.get(Model, ...)`` here: ORM entity loading would request prompt,
    # profile or other columns that the adapter must never be able to read.
    owner = session.execute(
        select(
            User.id,
            User.account_status,
            User.deletion_epoch,
            User.deleted_at,
        ).where(User.id == context.owner_user_id)
    ).mappings().one_or_none()
    job = session.execute(
        select(
            Job.id,
            Job.owner_user_id,
            Job.project_id,
            Job.status,
            Job.execution_fence,
            Job.owner_deletion_epoch,
            Job.analysis_input_version,
            Job.active_lease_id,
        ).where(Job.id == context.job_id)
    ).mappings().one_or_none()
    question_current = False
    source_active = False
    current_decision_version = 0
    open_action = False

    if owner is not None and job is not None:
        question_current = _question_is_current(
            session=session,
            context=context,
            job=job,
        )
        source_active = _source_is_active(
            session=session,
            context=context,
            job=job,
        )
        open_action = _has_current_open_action(session=session, context=context, job=job)
        current_decision_version = _current_decision_version(session=session, context=context)

    revoked = (
        context.revoked_at is not None
        or owner is None
        or job is None
        or owner["account_status"] != "ACTIVE"
        or owner["deleted_at"] is not None
        # A job's stored owner epoch is a historical binding, not proof that the
        # owner has not entered a newer deletion epoch since this context was
        # issued.  Check the current owner state directly so W4's *first*
        # resolve after that change is fail-closed, before it can create or send
        # a new durable outbox entry.
        or owner["deletion_epoch"] != context.owner_deletion_epoch
        or job["owner_user_id"] != context.owner_user_id
        or job["owner_deletion_epoch"] != context.owner_deletion_epoch
        or job["execution_fence"] != context.execution_fence
        or job["analysis_input_version"] != context.analysis_input_version
        or job["status"] != "WAITING_USER"
        or job["active_lease_id"] is not None
    )
    now = datetime.now(UTC)
    processing_allowed = (
        context.data_kind == "SYNTHETIC"
        and not revoked
        and now < context.expires_at
        and question_current
        and source_active
        and open_action
    )
    return W4QuestionCoreResolvedContext(
        context_key=context.context_key,
        job_id=context.job_id,
        question_version_id=context.question_version_id,
        source_id=context.source_id,
        analysis_input_version=context.analysis_input_version,
        authorization_revision=_authorization_revision(
            context=context,
            owner=owner,
            job=job,
            question_current=question_current,
            source_active=source_active,
            open_action=open_action,
            current_decision_version=current_decision_version,
            revoked=revoked,
        ),
        current_decision_version=current_decision_version,
        data_kind=context.data_kind,  # type: ignore[arg-type]
        processing_allowed=processing_allowed,
        question_current=question_current,
        source_active=source_active,
        revoked=revoked,
        valid_until=context.expires_at,
    )


def _question_is_current(
    *, session: Session, context: W4QuestionCoreContext, job: Mapping[str, object]
) -> bool:
    project_id = job["project_id"]
    if project_id is None:
        return False
    project = session.execute(
        select(ApplicationProject.id, ApplicationProject.current_version_id).where(
            ApplicationProject.id == project_id,
            ApplicationProject.owner_user_id == context.owner_user_id,
        )
    ).mappings().one_or_none()
    if project is None or project["current_version_id"] is None:
        return False
    project_version = session.execute(
        select(ApplicationProjectVersion.id).where(
            ApplicationProjectVersion.id == project["current_version_id"],
            ApplicationProjectVersion.project_id == project["id"],
            ApplicationProjectVersion.owner_user_id == context.owner_user_id,
        )
    ).mappings().one_or_none()
    question_version = session.execute(
        select(QuestionVersion.id, QuestionVersion.question_id).where(
            QuestionVersion.id == context.question_version_id,
            QuestionVersion.project_id == project["id"],
            QuestionVersion.owner_user_id == context.owner_user_id,
        )
    ).mappings().one_or_none()
    if project_version is None or question_version is None:
        return False
    question = session.execute(
        select(ProjectQuestion.id).where(
            ProjectQuestion.id == question_version["question_id"],
            ProjectQuestion.project_id == project["id"],
            ProjectQuestion.owner_user_id == context.owner_user_id,
            ProjectQuestion.current_version_id == question_version["id"],
            ProjectQuestion.status == "ACTIVE",
        )
    ).mappings().one_or_none()
    return question is not None


def _source_is_active(
    *, session: Session, context: W4QuestionCoreContext, job: Mapping[str, object]
) -> bool:
    project_id = job["project_id"]
    if project_id is None:
        return False
    project = session.execute(
        select(ApplicationProject.current_version_id).where(
            ApplicationProject.id == project_id,
            ApplicationProject.owner_user_id == context.owner_user_id,
        )
    ).mappings().one_or_none()
    if project is None or project["current_version_id"] is None:
        return False
    project_version = session.execute(
        select(ApplicationProjectVersion.company_id).where(
            ApplicationProjectVersion.id == project["current_version_id"],
            ApplicationProjectVersion.project_id == project_id,
            ApplicationProjectVersion.owner_user_id == context.owner_user_id,
        )
    ).mappings().one_or_none()
    link = session.execute(
        select(JobSourceLink.id).where(
            JobSourceLink.job_id == job["id"],
            JobSourceLink.owner_user_id == context.owner_user_id,
            JobSourceLink.source_id == context.source_id,
            JobSourceLink.analysis_input_version == context.analysis_input_version,
        )
    ).mappings().one_or_none()
    source = session.execute(
        select(Source.id, Source.company_id).where(Source.id == context.source_id)
    ).mappings().one_or_none()
    return (
        project_version is not None
        and link is not None
        and source is not None
        and source["company_id"] == project_version["company_id"]
    )


def _has_current_open_action(
    *, session: Session, context: W4QuestionCoreContext, job: Mapping[str, object]
) -> bool:
    return session.scalar(
        select(JobRequiredAction.id).where(
            JobRequiredAction.job_id == job["id"],
            JobRequiredAction.owner_user_id == context.owner_user_id,
            JobRequiredAction.action_code == W4_QUESTION_CORE_CONTEXT_ACTION,
            JobRequiredAction.action_status == "OPEN",
            JobRequiredAction.resolved_at.is_(None),
            JobRequiredAction.expected_input_version == context.analysis_input_version,
        )
    ) is not None


def _current_decision_version(*, session: Session, context: W4QuestionCoreContext) -> int:
    value = session.scalar(
        select(func.max(JobCoreDecisionBinding.decision_version)).where(
            JobCoreDecisionBinding.job_id == context.job_id,
            JobCoreDecisionBinding.source_id == context.source_id,
            JobCoreDecisionBinding.analysis_input_version == context.analysis_input_version,
            JobCoreDecisionBinding.origin_producer == W4_QUESTION_CORE_PRODUCER,
            JobCoreDecisionBinding.decision_scope == QUESTION_MATCHING_SCOPE,
            JobCoreDecisionBinding.question_version_id == context.question_version_id,
        )
    )
    return int(value or 0)


def _authorization_revision(
    *,
    context: W4QuestionCoreContext,
    owner: Mapping[str, object] | None,
    job: Mapping[str, object] | None,
    question_current: bool,
    source_active: bool,
    open_action: bool,
    current_decision_version: int,
    revoked: bool,
) -> str:
    """Return an opaque change marker without exposing owner or company identifiers."""

    state = {
        "context_key": str(context.context_key),
        "job_id": str(context.job_id),
        "question_version_id": str(context.question_version_id),
        "source_id": str(context.source_id),
        "analysis_input_version": context.analysis_input_version,
        "context_execution_fence": context.execution_fence,
        "current_execution_fence": job["execution_fence"] if job is not None else None,
        "context_deletion_epoch": context.owner_deletion_epoch,
        "current_deletion_epoch": owner["deletion_epoch"] if owner is not None else None,
        "job_status": job["status"] if job is not None else None,
        "has_active_lease": job["active_lease_id"] is not None if job is not None else None,
        "question_current": question_current,
        "source_active": source_active,
        "open_action": open_action,
        "current_decision_version": current_decision_version,
        "revoked": revoked,
    }
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
