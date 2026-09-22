from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.jobs import OutboxMessage
from app.repo.deletion import DeletionRepository
from app.services.jobs import JobService
from app.services.w2_commit_gate import W2CommitGateService

_DEFAULT_PREVIEW_TTL: Final = timedelta(minutes=30)
_DELETION_STORES: Final = ("POSTGRESQL", "NEO4J", "VECTOR", "CACHE", "CHECKPOINT")
_ACTIVE_REQUEST_STATUSES: Final = frozenset(
    {"REQUESTED", "CONFIRMED", "RUNNING", "PARTIALLY_COMPLETED", "FAILED_RETRYABLE"}
)
_RUNNING_REQUEST_STATUSES: Final = frozenset({"RUNNING", "PARTIALLY_COMPLETED", "FAILED_RETRYABLE"})


class DeletionOrchestrationError(Exception):
    pass


class DeletionRequestNotFoundError(DeletionOrchestrationError):
    pass


class DeletionValidationError(DeletionOrchestrationError):
    pass


class DeletionConflictError(DeletionOrchestrationError):
    pass


class DeletionTransitionError(DeletionOrchestrationError):
    pass


class StaleDeletionAcknowledgementError(DeletionOrchestrationError):
    pass


@dataclass(frozen=True)
class DeletionPreview:
    request: DeletionRequest
    preview_token: str


class DeletionOrchestrationService:
    """Coordinates private-store deletion without ever deleting public company knowledge.

    All methods are transaction-scoped and deliberately do not commit.  The caller must
    keep the database transaction open through request confirmation/start so the epoch
    increment, Job fence invalidation, session revocation, targets, and private outbox
    records become durable together.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = DeletionRepository(session)
        self.jobs = JobService(session)

    def create_account_deletion_preview(
        self,
        *,
        owner_user_id: UUID,
        preview_token: str | None = None,
        preview_ttl: timedelta = _DEFAULT_PREVIEW_TTL,
    ) -> DeletionPreview:
        """Create a one-time confirmation preview and persist only its digest."""
        if preview_ttl <= timedelta(0):
            raise DeletionValidationError("preview TTL must be positive")
        self._require_owner_for_update(owner_user_id=owner_user_id)
        now = datetime.now(UTC)
        active = self.repository.get_active_request_for_owner(owner_user_id=owner_user_id)
        if active is not None and active.status == "REQUESTED" and active.preview_expires_at <= now:
            active.status = "EXPIRED"
            active.updated_at = now
            self.session.flush()
            active = None
        if active is not None:
            raise DeletionConflictError("an active deletion request already exists")
        token = preview_token or secrets.token_urlsafe(32)
        self._require_nonempty(token, "preview token")
        request = DeletionRequest(
            owner_user_id=owner_user_id,
            target_type="ACCOUNT",
            target_id=None,
            scope="ALL_PRIVATE_DATA",
            preview_token_hash=self._hash_token(token),
            preview_expires_at=now + preview_ttl,
            status="REQUESTED",
            requested_at=now,
            updated_at=now,
        )
        self.repository.add_request(request)
        self.session.flush()
        return DeletionPreview(request=request, preview_token=token)

    def confirm_deletion_preview(
        self, *, owner_user_id: UUID, deletion_request_id: UUID, preview_token: str
    ) -> DeletionRequest:
        """Consume the preview token once; starting execution remains explicit."""
        self._require_nonempty(preview_token, "preview token")
        self._require_owner_for_update(owner_user_id=owner_user_id)
        request = self._require_request_for_update(
            deletion_request_id=deletion_request_id, owner_user_id=owner_user_id
        )
        now = datetime.now(UTC)
        if request.status == "REQUESTED" and request.preview_expires_at <= now:
            request.status = "EXPIRED"
            request.updated_at = now
            self.session.flush()
            raise DeletionTransitionError("deletion confirmation preview has expired")
        if request.status != "REQUESTED" or request.token_consumed_at is not None:
            raise DeletionTransitionError("deletion confirmation preview is not available")
        if not hmac.compare_digest(request.preview_token_hash, self._hash_token(preview_token)):
            raise DeletionValidationError("deletion confirmation preview token is invalid")
        request.status = "CONFIRMED"
        request.confirmed_at = now
        request.token_consumed_at = now
        request.updated_at = now
        self.session.flush()
        return request

    def start_confirmed_deletion(
        self, *, owner_user_id: UUID, deletion_request_id: UUID
    ) -> DeletionRequest:
        """Atomically start a confirmed request and stage all mandatory store commands."""
        owner = self._require_owner_for_update(owner_user_id=owner_user_id)
        request = self._require_request_for_update(
            deletion_request_id=deletion_request_id, owner_user_id=owner_user_id
        )
        if request.status != "CONFIRMED":
            raise DeletionTransitionError("only a confirmed deletion request can start")
        return self._start_request(owner=owner, request=request)

    def confirm_and_start_account_deletion(
        self, *, owner_user_id: UUID, deletion_request_id: UUID, preview_token: str
    ) -> DeletionRequest:
        """Convenience path for the API's confirmation action, in its single transaction."""
        self.confirm_deletion_preview(
            owner_user_id=owner_user_id,
            deletion_request_id=deletion_request_id,
            preview_token=preview_token,
        )
        return self.start_confirmed_deletion(
            owner_user_id=owner_user_id, deletion_request_id=deletion_request_id
        )

    def mark_target_dispatched(
        self, *, owner_user_id: UUID, deletion_request_id: UUID, deletion_target_id: UUID
    ) -> DeletionTarget:
        """Record relay hand-off separately from API acceptance and from store ACK."""
        request, target = self._require_running_target(
            owner_user_id=owner_user_id,
            deletion_request_id=deletion_request_id,
            deletion_target_id=deletion_target_id,
        )
        if target.status == "QUEUED":
            target.status = "DISPATCHED"
            target.error_code = None
            target.updated_at = datetime.now(UTC)
            request.updated_at = target.updated_at
            self.session.flush()
        return target

    def acknowledge_target(
        self,
        *,
        owner_user_id: UUID,
        deletion_request_id: UUID,
        deletion_target_id: UUID,
        ack_epoch: int,
        ack_event_id: UUID,
    ) -> DeletionRequest:
        """Accept one idempotent store ACK only when its deletion epoch is current."""
        request, target = self._require_running_target(
            owner_user_id=owner_user_id,
            deletion_request_id=deletion_request_id,
            deletion_target_id=deletion_target_id,
        )
        self._require_current_epoch(
            request=request, owner_user_id=owner_user_id, ack_epoch=ack_epoch
        )
        if target.status == "ACKNOWLEDGED":
            if target.ack_epoch == ack_epoch and target.ack_event_id == ack_event_id:
                return request
            raise DeletionConflictError("deletion target was already acknowledged by another event")
        now = datetime.now(UTC)
        target.status = "ACKNOWLEDGED"
        target.ack_epoch = ack_epoch
        target.ack_event_id = ack_event_id
        target.error_code = None
        target.completed_at = now
        target.updated_at = now
        self.session.flush()
        self._refresh_request_completion(request=request, owner_user_id=owner_user_id, now=now)
        self.session.flush()
        return request

    def record_target_failure(
        self,
        *,
        owner_user_id: UUID,
        deletion_request_id: UUID,
        deletion_target_id: UUID,
        failure_code: str,
    ) -> DeletionRequest:
        request, target = self._require_running_target(
            owner_user_id=owner_user_id,
            deletion_request_id=deletion_request_id,
            deletion_target_id=deletion_target_id,
        )
        self._require_nonempty(failure_code, "failure code")
        if target.status == "ACKNOWLEDGED":
            raise DeletionTransitionError("an acknowledged deletion target cannot fail")
        now = datetime.now(UTC)
        target.status = "FAILED_RETRYABLE"
        target.error_code = failure_code
        target.updated_at = now
        request.status = "FAILED_RETRYABLE"
        request.failure_code = failure_code
        request.updated_at = now
        self.session.flush()
        return request

    def retry_target(
        self,
        *,
        owner_user_id: UUID,
        deletion_request_id: UUID,
        deletion_target_id: UUID,
    ) -> DeletionTarget:
        """Retry exactly one failed private store; completed stores are never re-opened."""
        request, target = self._require_running_target(
            owner_user_id=owner_user_id,
            deletion_request_id=deletion_request_id,
            deletion_target_id=deletion_target_id,
        )
        if target.status != "FAILED_RETRYABLE":
            raise DeletionTransitionError("only a failed deletion target can be retried")
        target.status = "QUEUED"
        target.error_code = None
        target.updated_at = datetime.now(UTC)
        request.status = (
            "PARTIALLY_COMPLETED" if self._has_acknowledged_target(request.id) else "RUNNING"
        )
        request.failure_code = None
        request.updated_at = target.updated_at
        self._stage_target_dispatch(request=request, target=target)
        self.session.flush()
        return target

    def replay_after_restore(
        self, *, owner_user_id: UUID, deletion_request_id: UUID
    ) -> DeletionRequest:
        """Reapply a durable deletion after backup restore before normal serving resumes."""
        owner = self._require_owner_for_update(owner_user_id=owner_user_id)
        request = self._require_request_for_update(
            deletion_request_id=deletion_request_id, owner_user_id=owner_user_id
        )
        if (
            request.owner_deletion_epoch is None
            or request.status not in _RUNNING_REQUEST_STATUSES | {"COMPLETED"}
        ):
            raise DeletionTransitionError(
                "only a started deletion request can be replayed after restore"
            )
        owner.deletion_epoch = max(owner.deletion_epoch, request.owner_deletion_epoch)
        owner.account_status = "DELETION_PENDING"
        now = datetime.now(UTC)
        for auth_session in self.repository.get_open_auth_sessions_for_update(
            owner_user_id=owner_user_id
        ):
            auth_session.revoked_at = now
            auth_session.revoke_reason = "DELETION_PENDING"
        targets = self.repository.get_targets_for_update(deletion_request_id=request.id)
        for target in targets:
            target.status = "QUEUED"
            target.ack_epoch = None
            target.ack_event_id = None
            target.completed_at = None
            target.error_code = None
            target.updated_at = now
            self._stage_target_dispatch(request=request, target=target)
        request.status = "RUNNING"
        request.completed_at = None
        request.failure_code = None
        request.updated_at = now
        self.session.flush()
        return request

    def _start_request(self, *, owner: object, request: DeletionRequest) -> DeletionRequest:
        owner_epoch = getattr(owner, "deletion_epoch", None)
        if not isinstance(owner_epoch, int):
            raise DeletionRequestNotFoundError("deletion request owner does not exist")
        now = datetime.now(UTC)
        next_epoch = owner_epoch + 1
        owner.deletion_epoch = next_epoch
        owner.account_status = "DELETION_PENDING"
        request.owner_deletion_epoch = next_epoch
        request.status = "RUNNING"
        request.failure_code = None
        request.updated_at = now
        # Reconcile the private W2 visibility gate before this workflow takes
        # the broader active-Job locks below.  The owner epoch is already
        # advanced in this transaction: PREPARE-stage work becomes ABORT and
        # W1-committed private work becomes PURGE tied to this new epoch.
        for job_id in self.repository.list_w2_gate_job_ids(owner_user_id=request.owner_user_id):
            W2CommitGateService(self.session).reconcile_open_operations_for_owner_deletion(
                owner_user_id=request.owner_user_id,
                job_id=job_id,
                purge_owner_deletion_epoch=next_epoch,
            )
        for auth_session in self.repository.get_open_auth_sessions_for_update(
            owner_user_id=request.owner_user_id
        ):
            auth_session.revoked_at = now
            auth_session.revoke_reason = "DELETION_PENDING"
        for job in self.repository.get_active_jobs_for_update(owner_user_id=request.owner_user_id):
            self.jobs.invalidate_for_owner_deletion(
                owner_user_id=request.owner_user_id,
                job_id=job.id,
                owner_deletion_epoch=next_epoch,
            )
        # Both W3 COMPANY_KNOWLEDGE and W4 QUESTION_MATCHING bindings are private
        # owner-scoped authorization state.  Deleting them after each affected Job
        # is fenced prevents a late explicit retry, relay or lookup from restoring
        # the deleting owner's access.  The repository intentionally does not touch
        # Source or AnalysisSourceDecision rows because another owner may reference
        # the same public company Source.
        self.repository.delete_core_decision_bindings_for_owner(
            owner_user_id=request.owner_user_id
        )
        resource_id = request.target_id or request.owner_user_id
        if resource_id is None:
            raise DeletionValidationError("deletion request has no private subject reference")
        for store_type in _DELETION_STORES:
            target = DeletionTarget(
                deletion_request_id=request.id,
                store_type=store_type,
                resource_type="OWNER_PRIVATE_SCOPE",
                resource_id=resource_id,
                status="QUEUED",
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            self.repository.add_target(target)
        self.session.flush()
        for target in self.repository.get_targets_for_update(deletion_request_id=request.id):
            self._stage_target_dispatch(request=request, target=target)
        self.session.flush()
        return request

    def _stage_target_dispatch(
        self, *, request: DeletionRequest, target: DeletionTarget
    ) -> OutboxMessage:
        if request.owner_user_id is None or request.owner_deletion_epoch is None:
            raise DeletionValidationError(
                "deletion dispatch requires a live owner and deletion epoch"
            )
        target.attempts += 1
        target.status = "QUEUED"
        target.updated_at = datetime.now(UTC)
        message = OutboxMessage(
            message_type="w1.deletion.command",
            schema_version="1.0",
            visibility_scope="PRIVATE",
            aggregate_type="DELETION_TARGET",
            aggregate_id=target.id,
            aggregate_revision=target.attempts,
            deletion_request_id=request.id,
            deletion_target_id=target.id,
            owner_user_id=request.owner_user_id,
            owner_deletion_epoch=request.owner_deletion_epoch,
            payload={},
        )
        self.repository.add_outbox_message(message)
        self.session.flush()
        message.payload = {
            "schema_version": "1.0",
            "command_id": str(message.id),
            "owner_id": str(request.owner_user_id),
            "owner_deletion_epoch": request.owner_deletion_epoch,
            "target_type": target.store_type,
            "target_ref": str(target.id),
        }
        return message

    def _require_running_target(
        self, *, owner_user_id: UUID, deletion_request_id: UUID, deletion_target_id: UUID
    ) -> tuple[DeletionRequest, DeletionTarget]:
        self._require_owner_for_update(owner_user_id=owner_user_id)
        request = self._require_request_for_update(
            deletion_request_id=deletion_request_id, owner_user_id=owner_user_id
        )
        if request.status not in _RUNNING_REQUEST_STATUSES:
            raise DeletionTransitionError("deletion request is not running")
        target = self.repository.get_target_for_update(
            deletion_request_id=deletion_request_id, deletion_target_id=deletion_target_id
        )
        if target is None:
            raise DeletionRequestNotFoundError("deletion target does not exist")
        return request, target

    def _refresh_request_completion(
        self, *, request: DeletionRequest, owner_user_id: UUID, now: datetime
    ) -> None:
        targets = self.repository.get_targets_for_update(deletion_request_id=request.id)
        if len(targets) != len(_DELETION_STORES):
            raise DeletionTransitionError("deletion request is missing a mandatory store target")
        if all(
            target.status == "ACKNOWLEDGED" and target.ack_epoch == request.owner_deletion_epoch
            for target in targets
        ):
            self._require_current_epoch(
                request=request,
                owner_user_id=owner_user_id,
                ack_epoch=request.owner_deletion_epoch,
            )
            request.status = "COMPLETED"
            request.completed_at = now
            request.failure_code = None
        else:
            request.status = "PARTIALLY_COMPLETED"
            request.completed_at = None
        request.updated_at = now

    def _has_acknowledged_target(self, deletion_request_id: UUID) -> bool:
        return any(
            target.status == "ACKNOWLEDGED"
            for target in self.repository.get_targets_for_update(
                deletion_request_id=deletion_request_id
            )
        )

    def _require_current_epoch(
        self, *, request: DeletionRequest, owner_user_id: UUID, ack_epoch: int | None
    ) -> None:
        owner = self._require_owner_for_update(owner_user_id=owner_user_id)
        if (
            ack_epoch is None
            or request.owner_deletion_epoch is None
            or ack_epoch != request.owner_deletion_epoch
            or owner.deletion_epoch != request.owner_deletion_epoch
        ):
            raise StaleDeletionAcknowledgementError("deletion acknowledgement epoch is stale")

    def _require_owner_for_update(self, *, owner_user_id: UUID) -> object:
        owner = self.repository.get_owner_for_update(owner_user_id=owner_user_id)
        if owner is None:
            raise DeletionRequestNotFoundError("deletion request owner does not exist")
        return owner

    def _require_request_for_update(
        self, *, deletion_request_id: UUID, owner_user_id: UUID
    ) -> DeletionRequest:
        request = self.repository.get_request_for_update(
            deletion_request_id=deletion_request_id, owner_user_id=owner_user_id
        )
        if request is None:
            raise DeletionRequestNotFoundError("deletion request does not exist for this owner")
        return request

    @staticmethod
    def _hash_token(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_nonempty(value: str, label: str) -> None:
        if not value.strip():
            raise DeletionValidationError(f"{label} is required")
