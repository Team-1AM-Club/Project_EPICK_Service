from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.identity import IdempotencyRecord
from app.models.jobs import Job
from app.repo.identity import IdentityRepository
from app.services.idempotency import IdempotencyService
from app.services.jobs import (
    JobActionStaleError,
    JobIdempotencyReplayIncompleteError,
    JobNotFoundError,
    JobService,
    JobTransitionError,
)


@dataclass(frozen=True)
class JobActionAcceptance:
    """The accepted Job state, without exposing its private command or outbox records."""

    job: Job
    idempotency_record: IdempotencyRecord
    response_status: int
    replayed: bool


class JobActionService:
    """HTTP-facing idempotency boundary for explicit Job actions.

    The service records only acceptance.  It never invokes a worker, an outbox relay, or an
    engine synchronously, so a ``202`` remains distinct from actual dispatch. A W3 Core Decision
    still requires this explicit user action boundary before ``JobService`` creates the next
    fenced command.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.jobs = JobService(session)
        self.idempotency = IdempotencyService(IdentityRepository(session))

    def submit(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        required_action_id: UUID,
        action_code: str,
        expected_input_version: str | None,
        expected_result_version: str | None,
        acknowledge_rate_limit: bool,
        idempotency_key: str,
        request_hash: str,
        path_scope: str,
        checkpoint_id: UUID | None = None,
    ) -> JobActionAcceptance:
        record, replayed = self._reserve(
            owner_user_id=owner_user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            path_scope=path_scope,
        )
        if replayed:
            return self._replay(record=record, owner_user_id=owner_user_id, job_id=job_id)

        accepted = self.jobs.apply_required_action(
            owner_user_id=owner_user_id,
            job_id=job_id,
            required_action_id=required_action_id,
            action_code=action_code,
            expected_input_version=expected_input_version,
            expected_result_version=expected_result_version,
            acknowledge_rate_limit=acknowledge_rate_limit,
            checkpoint_id=checkpoint_id,
        )
        return JobActionAcceptance(
            job=accepted.job,
            idempotency_record=record,
            response_status=202 if accepted.command is not None else 200,
            replayed=False,
        )

    def retry_from_checkpoint(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        required_action_id: UUID,
        from_stage: str,
        expected_input_version: str | None,
        expected_result_version: str | None,
        acknowledge_rate_limit: bool,
        idempotency_key: str,
        request_hash: str,
        path_scope: str,
    ) -> JobActionAcceptance:
        record, replayed = self._reserve(
            owner_user_id=owner_user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            path_scope=path_scope,
        )
        if replayed:
            return self._replay(record=record, owner_user_id=owner_user_id, job_id=job_id)

        job = self.jobs.repository.get_job_for_update(
            job_id=job_id, owner_user_id=owner_user_id
        )
        if job is None:
            raise JobNotFoundError("Job does not exist for this owner")
        checkpoint = self.jobs.repository.get_current_checkpoint(job=job, for_update=True)
        if checkpoint is None or checkpoint.resume_stage != from_stage:
            raise JobActionStaleError("the requested checkpoint is no longer current")
        return self.submit_without_reserving(
            owner_user_id=owner_user_id,
            job_id=job_id,
            required_action_id=required_action_id,
            action_code="RETRY",
            expected_input_version=expected_input_version,
            expected_result_version=expected_result_version,
            acknowledge_rate_limit=acknowledge_rate_limit,
            checkpoint_id=checkpoint.id,
            record=record,
        )

    def request_cancellation(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        idempotency_key: str,
        request_hash: str,
        path_scope: str,
    ) -> JobActionAcceptance:
        record, replayed = self._reserve(
            owner_user_id=owner_user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            path_scope=path_scope,
        )
        if replayed:
            return self._replay(record=record, owner_user_id=owner_user_id, job_id=job_id)

        # ``JobService`` enters the shared W2 commit-gate lock boundary before
        # it locks/invalidate the current JobCommand.  Do not take Job here
        # first: doing so would invert User -> Job -> W2 command -> operation.
        job = self.jobs.request_cancellation(owner_user_id=owner_user_id, job_id=job_id)
        response_status = 202 if job.status == "CANCEL_REQUESTED" else 200
        return JobActionAcceptance(
            job=job,
            idempotency_record=record,
            response_status=response_status,
            replayed=False,
        )

    def submit_without_reserving(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        required_action_id: UUID,
        action_code: str,
        expected_input_version: str | None,
        expected_result_version: str | None,
        acknowledge_rate_limit: bool,
        checkpoint_id: UUID | None,
        record: IdempotencyRecord,
    ) -> JobActionAcceptance:
        accepted = self.jobs.apply_required_action(
            owner_user_id=owner_user_id,
            job_id=job_id,
            required_action_id=required_action_id,
            action_code=action_code,
            expected_input_version=expected_input_version,
            expected_result_version=expected_result_version,
            acknowledge_rate_limit=acknowledge_rate_limit,
            checkpoint_id=checkpoint_id,
        )
        return JobActionAcceptance(
            job=accepted.job,
            idempotency_record=record,
            response_status=202 if accepted.command is not None else 200,
            replayed=False,
        )

    def _reserve(
        self,
        *,
        owner_user_id: UUID,
        idempotency_key: str,
        request_hash: str,
        path_scope: str,
    ) -> tuple[IdempotencyRecord, bool]:
        if not idempotency_key.strip() or not request_hash.strip():
            raise JobTransitionError("idempotency key and request hash are required")
        return self.idempotency.reserve(
            owner_user_id=owner_user_id,
            method="POST",
            path_scope=path_scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.api_idempotency_ttl_seconds),
        )

    def _replay(
        self,
        *,
        record: IdempotencyRecord,
        owner_user_id: UUID,
        job_id: UUID,
    ) -> JobActionAcceptance:
        if record.response_ref != f"job:{job_id}" or record.response_status is None:
            raise JobIdempotencyReplayIncompleteError(
                "replayed action has no matching Job response"
            )
        job = self.jobs.repository.get_job(job_id=job_id, owner_user_id=owner_user_id)
        if job is None:
            raise JobIdempotencyReplayIncompleteError("replayed action Job does not exist")
        return JobActionAcceptance(
            job=job,
            idempotency_record=record,
            response_status=record.response_status,
            replayed=True,
        )
