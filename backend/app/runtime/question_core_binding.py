from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    ProjectQuestion,
    QuestionVersion,
)
from app.models.identity import User
from app.models.jobs import Job, JobCoreDecisionBinding
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source

W4_QUESTION_CORE_PRODUCER = "w4"
QUESTION_MATCHING_SCOPE = "QUESTION_MATCHING"
SOURCE_COLLECTION_PURPOSE = "SOURCE_COLLECTION"


class QuestionCoreBindingError(ValueError):
    """A null-company W4 pin no longer resolves to one current W1 company."""

    def __init__(self, code: str = "QUESTION_CORE_BINDING_INVALID") -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class QuestionCollectionCompany:
    """The transient W1-only company projection for a QUESTION_MATCHING pin."""

    company_id: UUID
    source_link_id: UUID


def _one_or_invalid(statement: Any, *, session: Session, for_update: bool) -> Any:
    if for_update:
        statement = statement.with_for_update()
    value = session.scalar(statement)
    if value is None:
        raise QuestionCoreBindingError()
    return value


def resolve_question_collection_company(
    *,
    session: Session,
    owner: User,
    job: Job,
    decision: AnalysisSourceDecision,
    binding: JobCoreDecisionBinding,
    pin: Mapping[str, object],
    expected_command_id: UUID | None,
    for_update: bool,
) -> QuestionCollectionCompany:
    """Resolve a W4 null-company pin only through current W1-owned relations.

    The returned UUID is a convenience value for the W2 command; it is never written back into
    the immutable QUESTION_MATCHING decision or pin.  Callers must repeat this at every outward
    boundary, using ``for_update=True`` where their database role owns the transaction lock.
    """

    current_decision = _one_or_invalid(
        select(AnalysisSourceDecision).where(AnalysisSourceDecision.id == decision.id),
        session=session,
        for_update=for_update,
    )
    current_binding = _one_or_invalid(
        select(JobCoreDecisionBinding).where(JobCoreDecisionBinding.id == binding.id),
        session=session,
        for_update=for_update,
    )
    try:
        question_version_id = UUID(str(pin["question_version_id"]))
        source_id = UUID(str(pin["source_id"]))
        origin_message_id = UUID(str(pin["origin_message_id"]))
        decision_id = UUID(str(pin["decision_id"]))
        analysis_input_version = pin["analysis_input_version"]
        if (
            pin.get("decision_scope") != QUESTION_MATCHING_SCOPE
            or pin.get("company_id") is not None
            or not isinstance(analysis_input_version, str)
            or not analysis_input_version
            or job.project_id is None
            or owner.id != job.owner_user_id
            or owner.account_status in {"DELETION_PENDING", "DELETED"}
            or owner.deletion_epoch != job.owner_deletion_epoch
            or current_decision.id != decision_id
            or current_decision.decision_scope != QUESTION_MATCHING_SCOPE
            or current_decision.company_id is not None
            or current_decision.question_version_id != question_version_id
            or current_decision.source_id != source_id
            or current_decision.analysis_input_version != analysis_input_version
            or job.analysis_input_version != analysis_input_version
            or current_decision.decision_code != "CORE_REQUIRED"
            or current_decision.decision_owner != "W4"
            or current_binding.job_id != job.id
            or current_binding.owner_user_id != owner.id
            or current_binding.owner_deletion_epoch != owner.deletion_epoch
            or current_binding.analysis_source_decision_id != current_decision.id
            or current_binding.origin_producer != W4_QUESTION_CORE_PRODUCER
            or current_binding.origin_message_id != origin_message_id
            or current_binding.decision_scope != QUESTION_MATCHING_SCOPE
            or current_binding.question_version_id != question_version_id
            or current_binding.source_id != source_id
            or current_binding.analysis_input_version != analysis_input_version
            or current_binding.decision_version != current_decision.decision_version
            or current_binding.decision_code != "CORE_REQUIRED"
        ):
            raise QuestionCoreBindingError()
    except (AttributeError, KeyError, TypeError, ValueError, QuestionCoreBindingError) as error:
        if isinstance(error, QuestionCoreBindingError):
            raise
        raise QuestionCoreBindingError() from error

    project = _one_or_invalid(
        select(ApplicationProject).where(
            ApplicationProject.id == job.project_id,
            ApplicationProject.owner_user_id == owner.id,
        ),
        session=session,
        for_update=for_update,
    )
    if project.current_version_id is None:
        raise QuestionCoreBindingError()
    project_version = _one_or_invalid(
        select(ApplicationProjectVersion).where(
            ApplicationProjectVersion.id == project.current_version_id,
            ApplicationProjectVersion.project_id == project.id,
            ApplicationProjectVersion.owner_user_id == owner.id,
        ),
        session=session,
        for_update=for_update,
    )
    question_version = _one_or_invalid(
        select(QuestionVersion).where(
            QuestionVersion.id == question_version_id,
            QuestionVersion.project_id == project.id,
            QuestionVersion.owner_user_id == owner.id,
        ),
        session=session,
        for_update=for_update,
    )
    question = _one_or_invalid(
        select(ProjectQuestion).where(
            ProjectQuestion.id == question_version.question_id,
            ProjectQuestion.project_id == project.id,
            ProjectQuestion.owner_user_id == owner.id,
            ProjectQuestion.current_version_id == question_version.id,
            ProjectQuestion.status == "ACTIVE",
        ),
        session=session,
        for_update=for_update,
    )
    del question
    link_statement = select(JobSourceLink).where(
        JobSourceLink.job_id == job.id,
        JobSourceLink.owner_user_id == owner.id,
        JobSourceLink.source_id == source_id,
        JobSourceLink.purpose_ref == SOURCE_COLLECTION_PURPOSE,
        JobSourceLink.analysis_input_version == analysis_input_version,
    )
    if expected_command_id is None:
        link_statement = link_statement.where(JobSourceLink.command_id.is_(None))
    else:
        link_statement = link_statement.where(JobSourceLink.command_id == expected_command_id)
    source_link = _one_or_invalid(link_statement, session=session, for_update=for_update)
    source = _one_or_invalid(
        select(Source).where(
            Source.id == source_id,
            Source.company_id == project_version.company_id,
        ),
        session=session,
        for_update=for_update,
    )
    if source.company_id != project_version.company_id:
        raise QuestionCoreBindingError()
    return QuestionCollectionCompany(
        company_id=project_version.company_id,
        source_link_id=source_link.id,
    )


def _lookup_one_or_invalid(*, session: Session, statement: Any) -> Mapping[str, object]:
    value = session.execute(statement).mappings().one_or_none()
    if value is None:
        raise QuestionCoreBindingError()
    return value


def resolve_question_collection_company_for_lookup(
    *,
    session: Session,
    owner: Mapping[str, object],
    job: Mapping[str, object],
    command: Mapping[str, object],
    pin: Mapping[str, object],
    w2_command: Mapping[str, object],
) -> UUID:
    """Repeat W4's current relation checks using lookup-role column projections only."""

    try:
        question_version_id = UUID(str(pin["question_version_id"]))
        source_id = UUID(str(pin["source_id"]))
        decision_id = UUID(str(pin["decision_id"]))
        origin_message_id = UUID(str(pin["origin_message_id"]))
        command_id = UUID(str(command["id"]))
        job_id = UUID(str(job["id"]))
        owner_id = UUID(str(owner["id"]))
        project_id = UUID(str(job["project_id"]))
        analysis_input_version = pin["analysis_input_version"]
        if (
            pin.get("decision_scope") != QUESTION_MATCHING_SCOPE
            or pin.get("company_id") is not None
            or not isinstance(analysis_input_version, str)
            or not analysis_input_version
            or owner["account_status"] in {"DELETION_PENDING", "DELETED"}
            or owner["id"] != job["owner_user_id"]
            or owner["deletion_epoch"] != job["owner_deletion_epoch"]
            or command["job_id"] != job["id"]
            or command["owner_user_id"] != owner["id"]
            or command["analysis_source_decision_id"] != decision_id
            or job["analysis_input_version"] != analysis_input_version
        ):
            raise QuestionCoreBindingError()
    except (KeyError, TypeError, ValueError, QuestionCoreBindingError) as error:
        if isinstance(error, QuestionCoreBindingError):
            raise
        raise QuestionCoreBindingError() from error

    decision = _lookup_one_or_invalid(
        session=session,
        statement=select(
            AnalysisSourceDecision.id,
            AnalysisSourceDecision.decision_scope,
            AnalysisSourceDecision.company_id,
            AnalysisSourceDecision.question_version_id,
            AnalysisSourceDecision.source_id,
            AnalysisSourceDecision.analysis_input_version,
            AnalysisSourceDecision.decision_version,
            AnalysisSourceDecision.decision_code,
            AnalysisSourceDecision.decision_owner,
            AnalysisSourceDecision.reason_code,
        ).where(AnalysisSourceDecision.id == decision_id),
    )
    binding = _lookup_one_or_invalid(
        session=session,
        statement=select(
            JobCoreDecisionBinding.id,
            JobCoreDecisionBinding.job_id,
            JobCoreDecisionBinding.owner_user_id,
            JobCoreDecisionBinding.owner_deletion_epoch,
            JobCoreDecisionBinding.analysis_source_decision_id,
            JobCoreDecisionBinding.origin_producer,
            JobCoreDecisionBinding.origin_message_id,
            JobCoreDecisionBinding.decision_scope,
            JobCoreDecisionBinding.question_version_id,
            JobCoreDecisionBinding.source_id,
            JobCoreDecisionBinding.analysis_input_version,
            JobCoreDecisionBinding.decision_version,
            JobCoreDecisionBinding.decision_code,
        ).where(
            JobCoreDecisionBinding.job_id == job_id,
            JobCoreDecisionBinding.analysis_source_decision_id == decision_id,
        ),
    )
    project = _lookup_one_or_invalid(
        session=session,
        statement=select(
            ApplicationProject.id,
            ApplicationProject.owner_user_id,
            ApplicationProject.current_version_id,
        ).where(
            ApplicationProject.id == project_id,
            ApplicationProject.owner_user_id == owner_id,
        ),
    )
    if project["current_version_id"] is None:
        raise QuestionCoreBindingError()
    project_version = _lookup_one_or_invalid(
        session=session,
        statement=select(
            ApplicationProjectVersion.id,
            ApplicationProjectVersion.project_id,
            ApplicationProjectVersion.owner_user_id,
            ApplicationProjectVersion.company_id,
        ).where(
            ApplicationProjectVersion.id == project["current_version_id"],
            ApplicationProjectVersion.project_id == project_id,
            ApplicationProjectVersion.owner_user_id == owner_id,
        ),
    )
    question_version = _lookup_one_or_invalid(
        session=session,
        statement=select(
            QuestionVersion.id,
            QuestionVersion.question_id,
            QuestionVersion.project_id,
            QuestionVersion.owner_user_id,
        ).where(
            QuestionVersion.id == question_version_id,
            QuestionVersion.project_id == project_id,
            QuestionVersion.owner_user_id == owner_id,
        ),
    )
    _lookup_one_or_invalid(
        session=session,
        statement=select(ProjectQuestion.id).where(
            ProjectQuestion.id == question_version["question_id"],
            ProjectQuestion.project_id == project_id,
            ProjectQuestion.owner_user_id == owner_id,
            ProjectQuestion.current_version_id == question_version_id,
            ProjectQuestion.status == "ACTIVE",
        ),
    )
    link = _lookup_one_or_invalid(
        session=session,
        statement=select(
            JobSourceLink.id,
            JobSourceLink.job_id,
            JobSourceLink.owner_user_id,
            JobSourceLink.source_id,
            JobSourceLink.command_id,
            JobSourceLink.purpose_ref,
            JobSourceLink.analysis_input_version,
        ).where(
            JobSourceLink.job_id == job_id,
            JobSourceLink.owner_user_id == owner_id,
            JobSourceLink.source_id == source_id,
            JobSourceLink.command_id == command_id,
            JobSourceLink.purpose_ref == SOURCE_COLLECTION_PURPOSE,
            JobSourceLink.analysis_input_version == analysis_input_version,
        ),
    )
    del link
    _lookup_one_or_invalid(
        session=session,
        statement=select(Source.id).where(
            Source.id == source_id,
            Source.company_id == project_version["company_id"],
        ),
    )
    if (
        decision["decision_scope"] != QUESTION_MATCHING_SCOPE
        or decision["company_id"] is not None
        or decision["question_version_id"] != question_version_id
        or decision["source_id"] != source_id
        or decision["analysis_input_version"] != analysis_input_version
        or decision["decision_code"] != "CORE_REQUIRED"
        or decision["decision_owner"] != "W4"
        or binding["job_id"] != job_id
        or binding["owner_user_id"] != owner_id
        or binding["owner_deletion_epoch"] != owner["deletion_epoch"]
        or binding["analysis_source_decision_id"] != decision_id
        or binding["origin_producer"] != W4_QUESTION_CORE_PRODUCER
        or binding["origin_message_id"] != origin_message_id
        or binding["decision_scope"] != QUESTION_MATCHING_SCOPE
        or binding["question_version_id"] != question_version_id
        or binding["source_id"] != source_id
        or binding["analysis_input_version"] != analysis_input_version
        or binding["decision_version"] != decision["decision_version"]
        or binding["decision_code"] != "CORE_REQUIRED"
        or w2_command.get("company_id") != str(project_version["company_id"])
    ):
        raise QuestionCoreBindingError()
    return UUID(str(project_version["company_id"]))
