from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import sessionmaker

from app.db.session import set_local_owner_context
from app.repo.jobs import JobRepository
from app.services.jobs import JobService
from app.services.lifecycle_operations import LifecycleOperationsService


def accept_job(
    factory: sessionmaker,
    *,
    owner_user_id: UUID,
    key: str,
    job_type: str = "TEST_JOB",
) -> str:
    with factory.begin() as session:
        set_local_owner_context(session, owner_user_id)
        accepted = JobService(session).accept_job(
            owner_user_id=owner_user_id,
            job_type=job_type,
            idempotency_key=key,
            request_hash=f"hash-{key}",
            analysis_input_version="analysis-v1",
        )
        return str(accepted.job.id)


def pause_job_with_action(
    factory: sessionmaker,
    *,
    owner_user_id: UUID,
    key: str,
    status: str,
    action_code: str,
    checkpoint: bool = False,
) -> dict[str, str]:
    with factory.begin() as session:
        set_local_owner_context(session, owner_user_id)
        jobs = JobService(session)
        accepted = jobs.accept_job(
            owner_user_id=owner_user_id,
            job_type="SOURCE_COLLECTION",
            idempotency_key=key,
            request_hash=f"hash-{key}",
            analysis_input_version="analysis-v1",
        )
        jobs.mark_dispatch_enqueued(owner_user_id=owner_user_id, job_id=accepted.job.id)
        lease = jobs.claim_execution(owner_user_id=owner_user_id, job_id=accepted.job.id)
        assert lease is not None
        assert jobs.pause_execution(
            owner_user_id=owner_user_id,
            job_id=accepted.job.id,
            lease_id=lease.id,
            execution_fence=lease.execution_fence,
            owner_deletion_epoch=lease.owner_deletion_epoch,
            status=status,
            action_code=action_code,
            expected_input_version="analysis-v1",
            expected_result_version="result-v3",
        )
        if checkpoint:
            LifecycleOperationsService(session).create_job_checkpoint(
                owner_user_id=owner_user_id,
                job_id=accepted.job.id,
                execution_fence=accepted.job.execution_fence,
                owner_deletion_epoch=accepted.job.owner_deletion_epoch,
                analysis_input_version="analysis-v1",
                resume_stage="RETRIEVING_CANDIDATES",
                state_ref="safe:checkpoint-ref",
                resume_payload={"cursor": "opaque"},
            )
        action = JobRepository(session).list_open_required_actions(
            job_id=accepted.job.id,
            owner_user_id=owner_user_id,
        )[0]
        return {"job_id": str(accepted.job.id), "action_id": str(action.id)}
