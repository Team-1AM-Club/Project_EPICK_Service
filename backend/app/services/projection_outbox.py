from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models.jobs import OutboxMessage
from app.models.projection import ExperienceExclusion, ProjectionSyncState
from app.repo.projection import ProjectionRepository

_PUBLIC_PAYLOAD_FORBIDDEN_KEYS = frozenset(
    {
        "owner_id",
        "owner_user_id",
        "job_id",
        "command_id",
        "authenticated_owner_ref",
        "project_id",
        "auth_subject",
        "email",
        "checkpoint",
        "prompt",
        "response",
        "secret",
        "token",
    }
)
_PROJECTION_STATUSES = frozenset({"PENDING", "STALE", "ERROR", "SYNCED"})
_EXCLUSION_SCOPES = frozenset({"GLOBAL", "COMPANY", "ROLE", "PROJECT"})


class ProjectionReadinessError(Exception):
    pass


class ProjectionNotFoundError(ProjectionReadinessError):
    pass


class ProjectionValidationError(ProjectionReadinessError):
    pass


class ProjectionOutboxService:
    """Transaction-scoped Projection readiness primitives with no external I/O.

    Callers perform their authoritative PostgreSQL write and call ``stage_public_projection`` on
    the same Session transaction.  This service never contacts SQS, Neo4j, Vector, W3, or W4.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = ProjectionRepository(session)

    def stage_public_projection(
        self,
        *,
        resource_type: str,
        resource_id: UUID,
        resource_version: UUID,
        projection_type: str,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int,
        message_type: str,
        payload: Mapping[str, object],
        schema_version: str = "1.0",
        event_id: UUID | None = None,
    ) -> OutboxMessage:
        """Stage a public projection event and its PENDING state in this transaction.

        A duplicate aggregate revision returns the already durable event.  A revision lower than
        the one successfully applied to the same resource/version cannot create a new event or
        regress the state.
        """

        self._validate_stage_input(
            resource_type=resource_type,
            projection_type=projection_type,
            aggregate_type=aggregate_type,
            aggregate_revision=aggregate_revision,
            message_type=message_type,
            schema_version=schema_version,
            payload=payload,
        )
        state = self.repository.get_projection_state_for_update(
            resource_type=resource_type,
            resource_id=resource_id,
            resource_version=resource_version,
            projection_type=projection_type,
        )
        if state is not None and state.last_applied_revision is not None:
            if aggregate_revision <= state.last_applied_revision:
                existing = self.repository.get_public_outbox_message(
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    aggregate_revision=aggregate_revision,
                    message_type=message_type,
                )
                if existing is not None:
                    return existing
                raise ProjectionValidationError(
                    "a projection revision at or below the applied revision cannot be staged"
                )

        existing = self.repository.get_public_outbox_message(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_revision=aggregate_revision,
            message_type=message_type,
        )
        if existing is not None:
            return existing

        message = OutboxMessage(
            id=event_id or uuid4(),
            message_type=message_type,
            schema_version=schema_version,
            visibility_scope="PUBLIC",
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_revision=aggregate_revision,
            payload=dict(payload),
        )
        self.repository.add_outbox_message(message)
        now = datetime.now(UTC)
        if state is None:
            state = ProjectionSyncState(
                resource_type=resource_type,
                resource_id=resource_id,
                resource_version=resource_version,
                projection_type=projection_type,
                status="PENDING",
                last_event_id=message.id,
            )
            self.repository.add_projection_state(state)
        else:
            state.status = "PENDING"
            state.last_event_id = message.id
            state.error_code = None
            state.synced_at = None
            state.updated_at = now
        self.session.flush()
        return message

    def claim_public_outbox_messages(self, *, limit: int) -> list[OutboxMessage]:
        """Lock and mark due public rows for one relay attempt; no broker call is made here."""

        if not isinstance(limit, int) or limit <= 0:
            raise ProjectionValidationError("claim limit must be positive")
        messages = self.repository.claim_pending_public_messages(limit=limit)
        for message in messages:
            message.status = "PUBLISHING"
            message.attempts += 1
        self.session.flush()
        return messages

    def mark_public_outbox_published(self, *, event_id: UUID) -> OutboxMessage:
        message = self._require_public_outbox_message(event_id=event_id)
        if message.status != "PUBLISHING":
            raise ProjectionValidationError("only a claimed public event can be published")
        message.status = "PUBLISHED"
        message.published_at = datetime.now(UTC)
        self.session.flush()
        return message

    def mark_public_outbox_retryable(
        self, *, event_id: UUID, available_at: datetime
    ) -> OutboxMessage:
        message = self._require_public_outbox_message(event_id=event_id)
        if message.status != "PUBLISHING":
            raise ProjectionValidationError("only a claimed public event can be retried")
        message.status = "FAILED_RETRYABLE"
        message.available_at = available_at
        self.session.flush()
        return message

    def mark_public_outbox_failed_final(self, *, event_id: UUID) -> OutboxMessage:
        """End delivery attempts for a claimed public event without mutating its source fact."""

        message = self._require_public_outbox_message(event_id=event_id)
        if message.status != "PUBLISHING":
            raise ProjectionValidationError("only a claimed public event can fail finally")
        message.status = "FAILED_FINAL"
        self.session.flush()
        return message

    def record_inbox_receipt(
        self, *, consumer_name: str, event_id: UUID, outcome_code: str
    ) -> bool:
        self._require_nonempty(consumer_name, "consumer name")
        self._require_nonempty(outcome_code, "outcome code")
        return self.repository.record_inbox_receipt(
            consumer_name=consumer_name,
            event_id=event_id,
            outcome_code=outcome_code,
        )

    def record_projection_outcome(
        self,
        *,
        resource_type: str,
        resource_id: UUID,
        resource_version: UUID,
        projection_type: str,
        event_id: UUID,
        aggregate_revision: int,
        status: str,
        error_code: str | None = None,
    ) -> ProjectionSyncState:
        if status not in _PROJECTION_STATUSES - {"PENDING"}:
            raise ProjectionValidationError("projection outcome must be STALE, ERROR, or SYNCED")
        if not isinstance(aggregate_revision, int) or aggregate_revision < 1:
            raise ProjectionValidationError("aggregate revision must be positive")
        self._require_nonempty(resource_type, "resource type")
        self._require_nonempty(projection_type, "projection type")
        if status == "ERROR":
            self._require_nonempty(error_code, "error code")
        elif error_code is not None:
            raise ProjectionValidationError("only ERROR may carry an error code")

        state = self.repository.get_projection_state_for_update(
            resource_type=resource_type,
            resource_id=resource_id,
            resource_version=resource_version,
            projection_type=projection_type,
        )
        if state is None:
            raise ProjectionNotFoundError("projection state does not exist")
        if (
            state.last_applied_revision is not None
            and aggregate_revision <= state.last_applied_revision
        ):
            return state

        now = datetime.now(UTC)
        state.last_event_id = event_id
        state.status = status
        state.updated_at = now
        if status == "SYNCED":
            state.last_applied_revision = aggregate_revision
            state.error_code = None
            state.synced_at = now
        elif status == "ERROR":
            state.error_code = error_code
            state.synced_at = None
        else:
            state.error_code = None
            state.synced_at = None
        self.session.flush()
        return state

    def create_experience_exclusion(
        self,
        *,
        owner_user_id: UUID,
        scope: str,
        activity_id: UUID | None = None,
        episode_id: UUID | None = None,
        company_id: UUID | None = None,
        role_id: UUID | None = None,
        project_id: UUID | None = None,
        reason: str | None = None,
    ) -> ExperienceExclusion:
        if scope not in _EXCLUSION_SCOPES:
            raise ProjectionValidationError("unknown exclusion scope")
        if (activity_id is None) == (episode_id is None):
            raise ProjectionValidationError(
                "an exclusion must target exactly one Activity or Episode"
            )
        expected_context = {
            "GLOBAL": (company_id is None and role_id is None and project_id is None),
            "COMPANY": (company_id is not None and role_id is None and project_id is None),
            "ROLE": (company_id is None and role_id is not None and project_id is None),
            "PROJECT": (company_id is None and role_id is None and project_id is not None),
        }
        if not expected_context[scope]:
            raise ProjectionValidationError("exclusion context does not match its scope")
        if reason is not None and (not reason.strip() or len(reason.encode()) > 1024):
            raise ProjectionValidationError("exclusion reason must be non-empty and at most 1 KiB")
        exclusion = ExperienceExclusion(
            owner_user_id=owner_user_id,
            activity_id=activity_id,
            episode_id=episode_id,
            scope=scope,
            company_id=company_id,
            role_id=role_id,
            project_id=project_id,
            reason=reason,
        )
        self.repository.add_exclusion(exclusion)
        self.session.flush()
        return exclusion

    def revoke_experience_exclusion(
        self, *, owner_user_id: UUID, exclusion_id: UUID
    ) -> ExperienceExclusion:
        exclusion = self.repository.get_exclusion_for_update(
            exclusion_id=exclusion_id, owner_user_id=owner_user_id
        )
        if exclusion is None:
            raise ProjectionNotFoundError("experience exclusion does not exist for this owner")
        if exclusion.revoked_at is None:
            exclusion.revoked_at = datetime.now(UTC)
            self.session.flush()
        return exclusion

    def _require_public_outbox_message(self, *, event_id: UUID) -> OutboxMessage:
        message = self.repository.get_public_outbox_message_for_update(event_id=event_id)
        if message is None:
            raise ProjectionNotFoundError("public outbox event does not exist")
        return message

    @classmethod
    def _validate_stage_input(
        cls,
        *,
        resource_type: str,
        projection_type: str,
        aggregate_type: str,
        aggregate_revision: int,
        message_type: str,
        schema_version: str,
        payload: Mapping[str, object],
    ) -> None:
        cls._require_nonempty(resource_type, "resource type")
        cls._require_nonempty(projection_type, "projection type")
        cls._require_nonempty(aggregate_type, "aggregate type")
        cls._require_nonempty(message_type, "message type")
        cls._require_nonempty(schema_version, "schema version")
        if not isinstance(aggregate_revision, int) or aggregate_revision < 1:
            raise ProjectionValidationError("aggregate revision must be positive")
        if not isinstance(payload, Mapping):
            raise ProjectionValidationError("public payload must be an object")
        forbidden = _PUBLIC_PAYLOAD_FORBIDDEN_KEYS.intersection(payload)
        if forbidden:
            raise ProjectionValidationError(
                "public payload contains private correlation or content"
            )

    @staticmethod
    def _require_nonempty(value: object, label: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ProjectionValidationError(f"{label} must be present")
