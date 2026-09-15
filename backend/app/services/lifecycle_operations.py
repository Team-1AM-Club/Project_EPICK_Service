from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.lifecycle_operations import (
    ExperienceDuplicateDecision,
    ExperienceDuplicateSuggestion,
    ExperienceMergeRecord,
    InferenceDecision,
    InferenceSuggestion,
    InferenceSuggestionSource,
    JobCheckpoint,
    Notification,
)
from app.repo.identity import IdentityRepository
from app.repo.jobs import JobRepository
from app.repo.lifecycle_operations import LifecycleOperationsRepository
from app.services.experience import ExperienceNotFoundError, ExperienceService
from app.services.jobs import JobAcceptance, JobService

_MAX_JSON_BYTES: Final = 16 * 1024
_CHECKPOINT_PAYLOAD_KEYS: Final = frozenset(
    {"cursor", "next_page", "snapshot_id", "source_version_id"}
)
_FORBIDDEN_JSON_KEYS: Final = frozenset(
    {
        "auth_subject",
        "email",
        "original_narrative",
        "password",
        "prompt",
        "response",
        "secret",
        "token",
    }
)


class LifecycleOperationError(Exception):
    pass


class LifecycleOperationNotFoundError(LifecycleOperationError):
    pass


class LifecycleOperationValidationError(LifecycleOperationError):
    pass


class LifecycleOperationTransitionError(LifecycleOperationError):
    pass


class LifecycleOperationsService:
    """Approval-gated inference, recovery, and notification orchestration.

    The service deliberately never commits.  Its caller supplies the owner-scoped
    transaction, so each durable change remains atomic with its surrounding API or
    worker result handling.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = LifecycleOperationsRepository(session)
        self.jobs = JobRepository(session)

    def create_inference_suggestion(
        self,
        *,
        owner_user_id: UUID,
        episode_id: UUID,
        episode_version_id: UUID,
        suggestion_type: str,
        proposed_value: dict[str, object],
        model_policy_version: str,
        model_execution_id: UUID | None = None,
    ) -> InferenceSuggestion:
        self._require_nonempty(suggestion_type, "suggestion type")
        self._require_nonempty(model_policy_version, "model policy version")
        self._validate_safe_json_object(proposed_value, label="proposed value")
        version = self.repository.get_episode_version(
            episode_id=episode_id,
            episode_version_id=episode_version_id,
            owner_user_id=owner_user_id,
        )
        if version is None:
            raise LifecycleOperationNotFoundError("episode version does not exist for this owner")
        suggestion = InferenceSuggestion(
            episode_id=episode_id,
            episode_version_id=episode_version_id,
            owner_user_id=owner_user_id,
            suggestion_type=suggestion_type,
            proposed_value=proposed_value,
            model_policy_version=model_policy_version,
            model_execution_id=model_execution_id,
        )
        self.repository.add_inference_suggestion(suggestion)
        self.session.flush()
        return suggestion

    def add_inference_suggestion_source(
        self,
        *,
        owner_user_id: UUID,
        suggestion_id: UUID,
        episode_version_id: UUID | None = None,
        source_version_id: UUID | None = None,
        evidence_span_id: UUID | None = None,
        field_name: str | None = None,
        source_span_start: int | None = None,
        source_span_end: int | None = None,
    ) -> InferenceSuggestionSource:
        if (
            sum(
                value is not None
                for value in (episode_version_id, source_version_id, evidence_span_id)
            )
            != 1
        ):
            raise LifecycleOperationValidationError("exactly one evidence reference is required")
        if source_span_start is not None and source_span_start < 0:
            raise LifecycleOperationValidationError("source span start cannot be negative")
        if source_span_end is not None and source_span_end < 0:
            raise LifecycleOperationValidationError("source span end cannot be negative")
        if (
            source_span_start is not None
            and source_span_end is not None
            and source_span_end < source_span_start
        ):
            raise LifecycleOperationValidationError("source span end must not precede its start")
        suggestion = self.repository.get_inference_suggestion_for_update(
            suggestion_id=suggestion_id, owner_user_id=owner_user_id
        )
        if suggestion is None:
            raise LifecycleOperationNotFoundError(
                "inference suggestion does not exist for this owner"
            )
        if episode_version_id is not None:
            version = self.repository.get_episode_version(
                episode_id=suggestion.episode_id,
                episode_version_id=episode_version_id,
                owner_user_id=owner_user_id,
            )
            if version is None:
                raise LifecycleOperationValidationError(
                    "episode evidence must belong to the suggestion owner"
                )
        source = InferenceSuggestionSource(
            suggestion_id=suggestion.id,
            owner_user_id=owner_user_id,
            episode_version_id=episode_version_id,
            source_version_id=source_version_id,
            evidence_span_id=evidence_span_id,
            field_name=field_name,
            source_span_start=source_span_start,
            source_span_end=source_span_end,
        )
        self.repository.add_inference_source(source)
        self.session.flush()
        return source

    def record_inference_decision(
        self,
        *,
        owner_user_id: UUID,
        suggestion_id: UUID,
        decision: str,
        modified_value: dict[str, object] | None = None,
        reason: str | None = None,
    ) -> InferenceDecision:
        if decision not in {"APPROVED", "MODIFIED", "REJECTED"}:
            raise LifecycleOperationValidationError("unknown inference decision")
        if decision == "MODIFIED" and modified_value is None:
            raise LifecycleOperationValidationError("a modified decision requires a value")
        if decision != "MODIFIED" and modified_value is not None:
            raise LifecycleOperationValidationError("only a modified decision may contain a value")
        if modified_value is not None:
            self._validate_safe_json_object(modified_value, label="modified value")
        suggestion = self.repository.get_inference_suggestion_for_update(
            suggestion_id=suggestion_id, owner_user_id=owner_user_id
        )
        if suggestion is None:
            raise LifecycleOperationNotFoundError(
                "inference suggestion does not exist for this owner"
            )
        record = InferenceDecision(
            suggestion_id=suggestion.id,
            owner_user_id=owner_user_id,
            decision_no=self.repository.next_inference_decision_no(suggestion_id=suggestion.id),
            decision=decision,
            modified_value=modified_value,
            reason=self._bounded_optional_text(reason, label="reason", maximum=4096),
        )
        self.repository.add_inference_decision(record)
        suggestion.status = "DECIDED"
        self.session.flush()
        return record

    def create_duplicate_suggestion(
        self,
        *,
        owner_user_id: UUID,
        first_episode_id: UUID,
        first_episode_version_id: UUID,
        second_episode_id: UUID,
        second_episode_version_id: UUID,
        reason: str,
        model_execution_id: UUID | None = None,
    ) -> ExperienceDuplicateSuggestion:
        self._require_nonempty(reason, "duplicate reason")
        if first_episode_id == second_episode_id:
            raise LifecycleOperationValidationError("a duplicate suggestion needs two Episodes")
        first = self.repository.get_episode_version(
            episode_id=first_episode_id,
            episode_version_id=first_episode_version_id,
            owner_user_id=owner_user_id,
        )
        second = self.repository.get_episode_version(
            episode_id=second_episode_id,
            episode_version_id=second_episode_version_id,
            owner_user_id=owner_user_id,
        )
        if first is None or second is None:
            raise LifecycleOperationNotFoundError(
                "duplicate Episode Versions do not exist for this owner"
            )
        if first.activity_id != second.activity_id:
            raise LifecycleOperationValidationError("only Episodes in one Activity can be merged")
        if first_episode_id.int > second_episode_id.int:
            first_episode_id, second_episode_id = second_episode_id, first_episode_id
            first_episode_version_id, second_episode_version_id = (
                second_episode_version_id,
                first_episode_version_id,
            )
        suggestion = ExperienceDuplicateSuggestion(
            owner_user_id=owner_user_id,
            left_episode_id=first_episode_id,
            left_episode_version_id=first_episode_version_id,
            right_episode_id=second_episode_id,
            right_episode_version_id=second_episode_version_id,
            reason=self._bounded_optional_text(reason, label="duplicate reason", maximum=4096)
            or "",
            model_execution_id=model_execution_id,
        )
        self.repository.add_duplicate_suggestion(suggestion)
        self.session.flush()
        return suggestion

    def record_duplicate_decision(
        self,
        *,
        owner_user_id: UUID,
        suggestion_id: UUID,
        decision: str,
        reason: str | None = None,
    ) -> ExperienceDuplicateDecision:
        if decision not in {"MERGE", "KEEP_SEPARATE", "DISMISSED"}:
            raise LifecycleOperationValidationError("unknown duplicate decision")
        suggestion = self.repository.get_duplicate_suggestion_for_update(
            suggestion_id=suggestion_id, owner_user_id=owner_user_id
        )
        if suggestion is None:
            raise LifecycleOperationNotFoundError(
                "duplicate suggestion does not exist for this owner"
            )
        record = ExperienceDuplicateDecision(
            suggestion_id=suggestion.id,
            owner_user_id=owner_user_id,
            decision_no=self.repository.next_duplicate_decision_no(suggestion_id=suggestion.id),
            decision=decision,
            reason=self._bounded_optional_text(reason, label="reason", maximum=4096),
        )
        self.repository.add_duplicate_decision(record)
        suggestion.status = "DECIDED"
        self.session.flush()
        return record

    def create_merge_record(
        self,
        *,
        owner_user_id: UUID,
        decision_id: UUID,
        source_episode_id: UUID,
        source_episode_version_id: UUID,
        target_episode_id: UUID,
        target_episode_version_id: UUID,
    ) -> ExperienceMergeRecord:
        if source_episode_id == target_episode_id:
            raise LifecycleOperationValidationError("merge source and target must differ")
        decision = self.repository.get_duplicate_decision_for_update(
            decision_id=decision_id, owner_user_id=owner_user_id
        )
        if decision is None:
            raise LifecycleOperationNotFoundError(
                "duplicate decision does not exist for this owner"
            )
        suggestion = self.repository.get_duplicate_suggestion_for_update(
            suggestion_id=decision.suggestion_id, owner_user_id=owner_user_id
        )
        if suggestion is None:
            raise LifecycleOperationNotFoundError(
                "duplicate suggestion does not exist for this owner"
            )
        latest = self.repository.get_latest_duplicate_decision(suggestion_id=suggestion.id)
        if latest is None or latest.id != decision.id or decision.decision != "MERGE":
            raise LifecycleOperationTransitionError(
                "only the current MERGE decision can create a merge"
            )
        if self.repository.get_merge_record_for_decision(decision_id=decision.id) is not None:
            raise LifecycleOperationTransitionError(
                "a merge record already exists for this decision"
            )
        expected_pairs = {
            (suggestion.left_episode_id, suggestion.left_episode_version_id),
            (suggestion.right_episode_id, suggestion.right_episode_version_id),
        }
        if {
            (source_episode_id, source_episode_version_id),
            (target_episode_id, target_episode_version_id),
        } != expected_pairs:
            raise LifecycleOperationValidationError(
                "merge records must use the suggested Episode Versions"
            )
        target = self.repository.get_episode_for_update(
            episode_id=target_episode_id, owner_user_id=owner_user_id
        )
        if target is None or target.current_version_id != target_episode_version_id:
            raise LifecycleOperationTransitionError("merge target has a newer Episode Version")
        source = self.repository.get_episode_version(
            episode_id=source_episode_id,
            episode_version_id=source_episode_version_id,
            owner_user_id=owner_user_id,
        )
        target_version = self.repository.get_episode_version(
            episode_id=target_episode_id,
            episode_version_id=target_episode_version_id,
            owner_user_id=owner_user_id,
        )
        if (
            source is None
            or target_version is None
            or source.activity_id != target_version.activity_id
        ):
            raise LifecycleOperationValidationError("merge Episodes must remain in one Activity")
        try:
            result = ExperienceService(self.session).append_episode_version(
                episode_id=target_episode_id,
                owner_user_id=owner_user_id,
                expected_lock_version=target.lock_version,
                change_reason=f"Approved duplicate merge decision {decision.id}",
            )
        except ExperienceNotFoundError as error:
            raise LifecycleOperationNotFoundError("merge target no longer exists") from error
        record = ExperienceMergeRecord(
            decision_id=decision.id,
            owner_user_id=owner_user_id,
            source_episode_id=source_episode_id,
            source_episode_version_id=source_episode_version_id,
            target_episode_id=target_episode_id,
            target_episode_version_id=target_episode_version_id,
            result_episode_version_id=result.id,
        )
        self.repository.add_merge_record(record)
        self.session.flush()
        return record

    def create_job_checkpoint(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        execution_fence: int,
        owner_deletion_epoch: int,
        analysis_input_version: str | None,
        resume_stage: str,
        state_ref: str | None = None,
        resume_payload: dict[str, object] | None = None,
        resumable: bool = True,
    ) -> JobCheckpoint:
        self._require_nonempty(resume_stage, "resume stage")
        payload = resume_payload or {}
        self._validate_checkpoint_payload(payload)
        job = self.jobs.get_job_for_update(job_id=job_id, owner_user_id=owner_user_id)
        if job is None:
            raise LifecycleOperationNotFoundError("Job does not exist for this owner")
        if (
            execution_fence != job.execution_fence
            or owner_deletion_epoch != job.owner_deletion_epoch
            or analysis_input_version != job.analysis_input_version
        ):
            raise LifecycleOperationTransitionError(
                "checkpoint must use the Job's current fence, epoch, and input version"
            )
        checkpoint = JobCheckpoint(
            job_id=job.id,
            owner_user_id=owner_user_id,
            checkpoint_revision=self.repository.next_checkpoint_revision(job_id=job.id),
            analysis_input_version=analysis_input_version,
            execution_fence=execution_fence,
            owner_deletion_epoch=owner_deletion_epoch,
            resume_stage=resume_stage,
            state_ref=self._bounded_optional_text(state_ref, label="state reference", maximum=512),
            resume_payload=payload,
            resumable=resumable,
        )
        self.repository.add_checkpoint(checkpoint)
        self.session.flush()
        return checkpoint

    def resume_rate_limited_job_from_checkpoint(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        checkpoint_id: UUID,
        from_stage: str,
        idempotency_key: str,
        request_hash: str,
    ) -> JobAcceptance:
        path_scope = f"/jobs/{job_id}/retry"
        existing = IdentityRepository(self.session).find_idempotency_record(
            owner_user_id, "POST", path_scope, idempotency_key
        )
        if existing is not None:
            # JobService checks request-hash compatibility and returns the original
            # acceptance before evaluating the later Job status.
            return JobService(self.session).retry_job(
                owner_user_id=owner_user_id,
                job_id=job_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                checkpoint_id=checkpoint_id,
            )
        checkpoint = self.repository.get_checkpoint_for_update(
            checkpoint_id=checkpoint_id, owner_user_id=owner_user_id
        )
        if checkpoint is None or checkpoint.job_id != job_id:
            raise LifecycleOperationNotFoundError("checkpoint does not exist for this Job")
        if not checkpoint.resumable or checkpoint.resume_stage != from_stage:
            raise LifecycleOperationTransitionError("checkpoint cannot resume the requested stage")
        job = self.jobs.get_job_for_update(job_id=job_id, owner_user_id=owner_user_id)
        if job is None:
            raise LifecycleOperationNotFoundError("Job does not exist for this owner")
        if (
            checkpoint.execution_fence != job.execution_fence
            or checkpoint.owner_deletion_epoch != job.owner_deletion_epoch
            or checkpoint.analysis_input_version != job.analysis_input_version
        ):
            raise LifecycleOperationTransitionError("checkpoint is stale for the current Job")
        return JobService(self.session).retry_job(
            owner_user_id=owner_user_id,
            job_id=job_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            checkpoint_id=checkpoint.id,
        )

    def create_notification(
        self,
        *,
        owner_user_id: UUID,
        notification_type: str,
        severity: str,
        title: str,
        safe_message: str,
        project_id: UUID | None = None,
        action_url: str | None = None,
    ) -> Notification:
        self._require_nonempty(notification_type, "notification type")
        if severity not in {"INFO", "WARNING", "ERROR"}:
            raise LifecycleOperationValidationError("unknown notification severity")
        self._bounded_required_text(title, label="title", maximum=512)
        self._bounded_required_text(safe_message, label="safe message", maximum=4096)
        if action_url is not None and (
            not action_url.startswith("/")
            or "?" in action_url
            or "#" in action_url
            or len(action_url.encode("utf-8")) > 2048
        ):
            raise LifecycleOperationValidationError(
                "notification action must be a safe relative path"
            )
        notification = Notification(
            owner_user_id=owner_user_id,
            project_id=project_id,
            notification_type=notification_type,
            severity=severity,
            title=title,
            safe_message=safe_message,
            action_url=action_url,
        )
        self.repository.add_notification(notification)
        self.session.flush()
        return notification

    def mark_notification_read(self, *, owner_user_id: UUID, notification_id: UUID) -> Notification:
        notification = self._require_notification_for_update(
            owner_user_id=owner_user_id, notification_id=notification_id
        )
        if notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            self.session.flush()
        return notification

    def archive_notification(self, *, owner_user_id: UUID, notification_id: UUID) -> Notification:
        notification = self._require_notification_for_update(
            owner_user_id=owner_user_id, notification_id=notification_id
        )
        if notification.archived_at is None:
            notification.archived_at = datetime.now(UTC)
            self.session.flush()
        return notification

    def _require_notification_for_update(
        self, *, owner_user_id: UUID, notification_id: UUID
    ) -> Notification:
        notification = self.repository.get_notification_for_update(
            notification_id=notification_id, owner_user_id=owner_user_id
        )
        if notification is None:
            raise LifecycleOperationNotFoundError("notification does not exist for this owner")
        return notification

    @staticmethod
    def _require_nonempty(value: str, label: str) -> None:
        if not value.strip():
            raise LifecycleOperationValidationError(f"{label} must be present")

    @classmethod
    def _validate_safe_json_object(cls, value: dict[str, object], *, label: str) -> None:
        if not isinstance(value, dict):
            raise LifecycleOperationValidationError(f"{label} must be a JSON object")
        cls._validate_json_value(value, label=label)
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_JSON_BYTES:
            raise LifecycleOperationValidationError(f"{label} exceeds 16 KiB")

    @classmethod
    def _validate_checkpoint_payload(cls, payload: dict[str, object]) -> None:
        cls._validate_safe_json_object(payload, label="checkpoint payload")
        unexpected = set(payload).difference(_CHECKPOINT_PAYLOAD_KEYS)
        if unexpected:
            raise LifecycleOperationValidationError("checkpoint payload includes a disallowed key")

    @classmethod
    def _validate_json_value(cls, value: object, *, label: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if not isinstance(key, str):
                    raise LifecycleOperationValidationError(f"{label} has a non-string key")
                if key.casefold() in _FORBIDDEN_JSON_KEYS:
                    raise LifecycleOperationValidationError(f"{label} includes a sensitive key")
                cls._validate_json_value(nested, label=label)
        elif isinstance(value, list):
            for nested in value:
                cls._validate_json_value(nested, label=label)
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise LifecycleOperationValidationError(f"{label} must contain JSON-compatible values")

    @classmethod
    def _bounded_required_text(cls, value: str, *, label: str, maximum: int) -> None:
        cls._require_nonempty(value, label)
        if len(value.encode("utf-8")) > maximum:
            raise LifecycleOperationValidationError(f"{label} exceeds its safe size limit")

    @classmethod
    def _bounded_optional_text(cls, value: str | None, *, label: str, maximum: int) -> str | None:
        if value is None:
            return None
        cls._bounded_required_text(value, label=label, maximum=maximum)
        return value
