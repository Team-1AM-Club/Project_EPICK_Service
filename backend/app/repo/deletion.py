from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.application_workspace import ApplicationProject
from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.identity import AuthSession, User
from app.models.jobs import Job, JobCoreDecisionBinding, OutboxMessage
from app.models.sources import JobSourceLink, SourceCollectionAttempt
from app.models.w2_commit_operations import W2CommitOperation, W2StagedResult


class DeletionRepository:
    """Locking persistence primitives for private-data deletion orchestration."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_request(self, request: DeletionRequest) -> None:
        self.session.add(request)

    def add_target(self, target: DeletionTarget) -> None:
        self.session.add(target)

    def add_outbox_message(self, message: OutboxMessage) -> None:
        self.session.add(message)

    def get_owner_for_update(self, *, owner_user_id: UUID) -> User | None:
        return self.session.scalar(select(User).where(User.id == owner_user_id).with_for_update())

    def get_project_for_update(
        self, *, owner_user_id: UUID, project_id: UUID
    ) -> ApplicationProject | None:
        return self.session.scalar(
            select(ApplicationProject)
            .where(
                ApplicationProject.id == project_id,
                ApplicationProject.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_request_for_update(
        self, *, deletion_request_id: UUID, owner_user_id: UUID | None = None
    ) -> DeletionRequest | None:
        statement = select(DeletionRequest).where(DeletionRequest.id == deletion_request_id)
        if owner_user_id is not None:
            statement = statement.where(DeletionRequest.owner_user_id == owner_user_id)
        return self.session.scalar(statement.with_for_update())

    def get_request(
        self, *, deletion_request_id: UUID, owner_user_id: UUID
    ) -> DeletionRequest | None:
        return self.session.scalar(
            select(DeletionRequest).where(
                DeletionRequest.id == deletion_request_id,
                DeletionRequest.owner_user_id == owner_user_id,
            )
        )

    def get_target_for_update(
        self, *, deletion_request_id: UUID, deletion_target_id: UUID
    ) -> DeletionTarget | None:
        return self.session.scalar(
            select(DeletionTarget)
            .where(
                DeletionTarget.id == deletion_target_id,
                DeletionTarget.deletion_request_id == deletion_request_id,
            )
            .with_for_update()
        )

    def get_targets_for_update(self, *, deletion_request_id: UUID) -> list[DeletionTarget]:
        return list(
            self.session.scalars(
                select(DeletionTarget)
                .where(DeletionTarget.deletion_request_id == deletion_request_id)
                .order_by(DeletionTarget.store_type, DeletionTarget.id)
                .with_for_update()
            )
        )

    def list_targets(
        self, *, deletion_request_id: UUID, owner_user_id: UUID
    ) -> list[DeletionTarget]:
        return list(
            self.session.scalars(
                select(DeletionTarget)
                .join(DeletionRequest, DeletionRequest.id == DeletionTarget.deletion_request_id)
                .where(
                    DeletionTarget.deletion_request_id == deletion_request_id,
                    DeletionRequest.owner_user_id == owner_user_id,
                )
                .order_by(DeletionTarget.store_type, DeletionTarget.id)
            )
        )

    def get_active_jobs_for_update(self, *, owner_user_id: UUID) -> list[Job]:
        terminal_statuses = ("SUCCEEDED", "FAILED_FINAL", "CANCELLED")
        return list(
            self.session.scalars(
                select(Job)
                .where(Job.owner_user_id == owner_user_id, Job.status.not_in(terminal_statuses))
                .order_by(Job.created_at, Job.id)
                .with_for_update()
            )
        )

    def list_w2_gate_job_ids(self, *, owner_user_id: UUID) -> list[UUID]:
        """Read owner-scoped gate IDs, including finalized results on terminal Jobs."""

        return list(
            self.session.scalars(
                select(W2CommitOperation.job_id)
                .where(
                    W2CommitOperation.owner_user_id == owner_user_id,
                    W2CommitOperation.state.in_(
                        (
                            "PREPARE_PENDING",
                            "PREPARED",
                            "W1_COMMITTED",
                            "FINALIZE_PENDING",
                            "FINALIZED",
                        )
                    ),
                )
                .distinct()
                .order_by(W2CommitOperation.job_id)
            )
        )

    def get_open_auth_sessions_for_update(self, *, owner_user_id: UUID) -> list[AuthSession]:
        return list(
            self.session.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == owner_user_id, AuthSession.revoked_at.is_(None))
                .order_by(AuthSession.issued_at, AuthSession.id)
                .with_for_update()
            )
        )

    def delete_core_decision_bindings_for_owner(self, *, owner_user_id: UUID) -> int:
        """Remove only the deleting owner's private Job bindings.

        Source and AnalysisSourceDecision rows are intentionally outside this owner-scoped
        operation because they may be shared by another owner's Job.
        """

        result = self.session.execute(
            delete(JobCoreDecisionBinding).where(
                JobCoreDecisionBinding.owner_user_id == owner_user_id
            )
        )
        return int(result.rowcount or 0)

    def delete_core_decision_bindings_for_project(
        self, *, owner_user_id: UUID, project_id: UUID
    ) -> int:
        project_job_ids = select(Job.id).where(
            Job.owner_user_id == owner_user_id, Job.project_id == project_id
        )
        result = self.session.execute(
            delete(JobCoreDecisionBinding).where(
                JobCoreDecisionBinding.owner_user_id == owner_user_id,
                JobCoreDecisionBinding.job_id.in_(project_job_ids),
            )
        )
        return int(result.rowcount or 0)

    def purge_w2_private_references(self, *, owner_user_id: UUID, project_id: UUID | None) -> None:
        """Remove only the scoped W1-private W2 references, not public Source history.

        Collection attempts are public Source observations. Detach their private
        Job/command references before deleting owner-scoped JobSourceLink rows.
        All mutations are part of the caller's deletion/ACK transaction.
        """
        job_ids = select(Job.id).where(Job.owner_user_id == owner_user_id)
        if project_id is not None:
            job_ids = job_ids.where(Job.project_id == project_id)
        link_ids = select(JobSourceLink.id).where(
            JobSourceLink.owner_user_id == owner_user_id,
            JobSourceLink.job_id.in_(job_ids),
        )
        self.session.execute(
            update(SourceCollectionAttempt)
            .where(SourceCollectionAttempt.job_source_link_id.in_(link_ids))
            .values(job_source_link_id=None, command_id=None)
        )
        self.session.execute(
            delete(JobSourceLink).where(
                JobSourceLink.owner_user_id == owner_user_id,
                JobSourceLink.job_id.in_(job_ids),
            )
        )
        operation_ids = select(W2CommitOperation.id).where(
            W2CommitOperation.owner_user_id == owner_user_id,
            W2CommitOperation.job_id.in_(job_ids),
        )
        now = datetime.now(UTC)
        self.session.execute(
            update(W2StagedResult)
            .where(
                W2StagedResult.owner_user_id == owner_user_id,
                W2StagedResult.operation_id.in_(operation_ids),
                W2StagedResult.payload_state == "ACTIVE",
            )
            .values(
                result_payload=None,
                payload_state="CLEARED",
                cleared_at=now,
                updated_at=now,
            )
        )

    def get_active_request_for_owner(self, *, owner_user_id: UUID) -> DeletionRequest | None:
        return self.session.scalar(
            select(DeletionRequest)
            .where(
                DeletionRequest.owner_user_id == owner_user_id,
                DeletionRequest.status.in_(
                    ("REQUESTED", "CONFIRMED", "RUNNING", "PARTIALLY_COMPLETED", "FAILED_RETRYABLE")
                ),
            )
            .order_by(DeletionRequest.requested_at.desc())
            .limit(1)
        )

    def has_unacknowledged_w2_target_for_owner(self, *, owner_user_id: UUID) -> bool:
        return (
            self.session.scalar(
                select(DeletionTarget.id)
                .join(DeletionRequest, DeletionRequest.id == DeletionTarget.deletion_request_id)
                .where(
                    DeletionRequest.owner_user_id == owner_user_id,
                    DeletionTarget.store_type == "W2_SOURCE_RUNTIME",
                    DeletionTarget.status != "ACKNOWLEDGED",
                )
                .limit(1)
            )
            is not None
        )

    def get_deletion_outbox_message_for_target(
        self, *, deletion_target_id: UUID, aggregate_revision: int
    ) -> OutboxMessage | None:
        return self.session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.deletion_target_id == deletion_target_id,
                OutboxMessage.aggregate_revision == aggregate_revision,
            )
        )
