from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.api.schemas.jobs import (
    JobCheckpointResponse,
    JobFailureResponse,
    JobInputRefResponse,
    JobListItemResponse,
    JobProgressResponse,
    JobRequiredActionResponse,
    JobResponse,
)
from app.models.application_workspace import ApplicationProjectVersion, QuestionVersion
from app.models.experience import EpisodeVersion
from app.models.job_postings import JobPostingVersion
from app.models.jobs import Job, JobInputRef
from app.models.recommendations import ProjectSnapshot
from app.models.sources import SourceVersion
from app.repo.jobs import JobRepository


def job_response(*, session: Session, repository: JobRepository, job: Job) -> JobResponse:
    checkpoint = repository.get_current_checkpoint(job=job)
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        completeness=job.completeness,
        dispatch_status=job.dispatch_status,
        stage=job.stage,
        input_refs=[
            _input_ref_response(session=session, input_ref=input_ref)
            for input_ref in repository.list_input_refs(
                job_id=job.id, owner_user_id=job.owner_user_id
            )
        ],
        progress=_progress_response(job),
        required_actions=[
            JobRequiredActionResponse(
                id=action.id,
                code=action.action_code,
                status="OPEN",
                context_code=action.context_code,
                expected_input_version=action.expected_input_version,
                expected_result_version=action.expected_result_version,
            )
            for action in repository.list_open_required_actions(
                job_id=job.id, owner_user_id=job.owner_user_id
            )
        ],
        checkpoint=JobCheckpointResponse(
            available=checkpoint is not None,
            last_completed_stage=checkpoint.resume_stage if checkpoint is not None else None,
            analysis_input_version=(
                checkpoint.analysis_input_version if checkpoint is not None else None
            ),
        ),
        failure=_failure_response(job),
        limitations=[],
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def job_list_item(*, repository: JobRepository, job: Job) -> JobListItemResponse:
    return JobListItemResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        completeness=job.completeness,
        dispatch_status=job.dispatch_status,
        stage=job.stage,
        progress=_progress_response(job),
        required_action_count=len(
            repository.list_open_required_actions(
                job_id=job.id, owner_user_id=job.owner_user_id
            )
        ),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def checkpoint_response(*, repository: JobRepository, job: Job) -> JobCheckpointResponse:
    checkpoint = repository.get_current_checkpoint(job=job)
    return JobCheckpointResponse(
        available=checkpoint is not None,
        last_completed_stage=checkpoint.resume_stage if checkpoint is not None else None,
        analysis_input_version=(
            checkpoint.analysis_input_version if checkpoint is not None else None
        ),
    )


def _progress_response(job: Job) -> JobProgressResponse:
    if job.total_units is None:
        percent = None
    elif job.total_units == 0:
        percent = 100
    else:
        percent = min(100, (job.completed_units * 100) // job.total_units)
    return JobProgressResponse(
        completed_units=job.completed_units,
        total_units=job.total_units,
        percent=percent,
    )


def _failure_response(job: Job) -> JobFailureResponse:
    retry_after_seconds: int | None = None
    if job.retry_after is not None:
        retry_after_seconds = max(0, int((job.retry_after - datetime.now(UTC)).total_seconds()))
    return JobFailureResponse(
        code=job.failure_code,
        message=job.safe_failure_message,
        retryable=job.retryable,
        retry_after_seconds=retry_after_seconds,
    )


def _input_ref_response(*, session: Session, input_ref: JobInputRef) -> JobInputRefResponse:
    if input_ref.project_version_id is not None:
        version = session.get(ApplicationProjectVersion, input_ref.project_version_id)
        return _version_ref("PROJECT_VERSION", input_ref.project_version_id, version)
    if input_ref.question_version_id is not None:
        version = session.get(QuestionVersion, input_ref.question_version_id)
        return _version_ref("QUESTION_VERSION", input_ref.question_version_id, version)
    if input_ref.episode_version_id is not None:
        version = session.get(EpisodeVersion, input_ref.episode_version_id)
        return _version_ref("EPISODE_VERSION", input_ref.episode_version_id, version)
    if input_ref.snapshot_id is not None:
        snapshot = session.get(ProjectSnapshot, input_ref.snapshot_id)
        return _version_ref("SNAPSHOT", input_ref.snapshot_id, snapshot, field="snapshot_no")
    if input_ref.source_version_id is not None:
        version = session.get(SourceVersion, input_ref.source_version_id)
        return _version_ref("SOURCE_VERSION", input_ref.source_version_id, version)
    if input_ref.job_posting_version_id is not None:
        version = session.get(JobPostingVersion, input_ref.job_posting_version_id)
        return _version_ref("JOB_POSTING_VERSION", input_ref.job_posting_version_id, version)
    if input_ref.policy_name is not None and input_ref.policy_version is not None:
        return JobInputRefResponse(
            type="POLICY",
            id=input_ref.policy_name,
            version=input_ref.policy_version,
        )
    raise RuntimeError("Job input reference has no public typed value")


def _version_ref(
    reference_type: str,
    resource_id: object,
    resource: object | None,
    *,
    field: str = "version_no",
) -> JobInputRefResponse:
    if resource is None:
        raise RuntimeError("Job input reference does not resolve to its immutable resource")
    return JobInputRefResponse(
        type=reference_type,
        id=resource_id,  # type: ignore[arg-type]
        version=getattr(resource, field),
    )
