from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.identity import AuthSession, User
from app.models.jobs import Job, OutboxMessage


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

    def get_request_for_update(
        self, *, deletion_request_id: UUID, owner_user_id: UUID | None = None
    ) -> DeletionRequest | None:
        statement = select(DeletionRequest).where(DeletionRequest.id == deletion_request_id)
        if owner_user_id is not None:
            statement = statement.where(DeletionRequest.owner_user_id == owner_user_id)
        return self.session.scalar(statement.with_for_update())

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

    def get_open_auth_sessions_for_update(self, *, owner_user_id: UUID) -> list[AuthSession]:
        return list(
            self.session.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == owner_user_id, AuthSession.revoked_at.is_(None))
                .order_by(AuthSession.issued_at, AuthSession.id)
                .with_for_update()
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

    def get_deletion_outbox_message_for_target(
        self, *, deletion_target_id: UUID, aggregate_revision: int
    ) -> OutboxMessage | None:
        return self.session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.deletion_target_id == deletion_target_id,
                OutboxMessage.aggregate_revision == aggregate_revision,
            )
        )
