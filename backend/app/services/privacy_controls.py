from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.privacy_controls import (
    AnalyticsEvent,
    Consent,
    Feedback,
    ProjectRecommendationPreference,
    RecommendationPreference,
    RetentionPreference,
    SensitivityAssessment,
    SensitivityDecision,
    SensitivityFinding,
    SnapshotRecommendationPreference,
    UserSettings,
)
from app.repo.privacy_controls import PrivacyControlsRepository

_DEFAULT_CANDIDATE_LIMIT: Final = 5
_DEFAULT_QUESTION_DISPLAY_MODE: Final = "SUMMARY_FIRST"
_DEFAULT_EVIDENCE_DISPLAY_MODE: Final = "ON_DEMAND"
_DEFAULT_SHOW_INFORMATION_COMPLETENESS: Final = False
_MAX_DISPLAY_OPTIONS_BYTES: Final = 4 * 1024
_MAX_OTHER_TEXT_BYTES: Final = 4 * 1024
_ANALYTICS_EVENT_PROPERTIES: Final = {
    "MATERIAL_SELECTION_SAVED": frozenset({"selection_set_id"}),
    "RECOMMENDATION_CANDIDATE_VIEWED": frozenset({"candidate_id"}),
    "RECOMMENDATION_EVIDENCE_VIEWED": frozenset({"candidate_id"}),
    "RECOMMENDATION_COMPLETED": frozenset({"run_id"}),
    "FEEDBACK_SUBMITTED": frozenset({"feedback_id"}),
}


class PrivacyControlsError(Exception):
    pass


class PrivacyControlsNotFoundError(PrivacyControlsError):
    pass


class PrivacyControlsValidationError(PrivacyControlsError):
    pass


class PrivacyControlsConflictError(PrivacyControlsError):
    pass


class ExternalProcessingBlockedError(PrivacyControlsError):
    pass


@dataclass(frozen=True)
class ExternalProcessingEligibility:
    eligible: bool
    mode: str | None
    reason_code: str
    assessment_id: UUID | None
    decision_id: UUID | None


class PrivacyControlsService:
    """Transaction-scoped settings, consent, and sensitivity-gate orchestration.

    The service records only safe metadata.  It never copies Experience text into a
    finding, analytics record, or external-processing eligibility response.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = PrivacyControlsRepository(session)

    def get_or_create_user_settings(self, *, owner_user_id: UUID) -> UserSettings:
        owner = self._require_owner_for_update(owner_user_id=owner_user_id)
        settings = self.repository.get_user_settings_for_update(owner_user_id=owner_user_id)
        if settings is not None:
            return settings
        settings = UserSettings(
            owner_user_id=owner_user_id,
            locale=owner.locale,
            timezone=owner.timezone,
            display_options={},
        )
        self.repository.add(settings)
        self.session.flush()
        return settings

    def update_user_settings(
        self,
        *,
        owner_user_id: UUID,
        expected_lock_version: int,
        locale: str | None = None,
        timezone: str | None = None,
        display_options: dict[str, object] | None = None,
    ) -> UserSettings:
        settings = self.get_or_create_user_settings(owner_user_id=owner_user_id)
        self._require_expected_lock(
            actual_lock_version=settings.lock_version,
            expected_lock_version=expected_lock_version,
            resource="user settings",
        )
        if locale is not None:
            self._require_nonempty(locale, "locale")
            settings.locale = locale
        if timezone is not None:
            self._require_nonempty(timezone, "timezone")
            settings.timezone = timezone
        if display_options is not None:
            self._validate_json_object(
                display_options, label="display options", maximum_bytes=_MAX_DISPLAY_OPTIONS_BYTES
            )
            settings.display_options = display_options
        settings.lock_version += 1
        settings.updated_at = datetime.now(UTC)
        self.session.flush()
        return settings

    def get_or_create_recommendation_preferences(
        self, *, owner_user_id: UUID
    ) -> RecommendationPreference:
        self._require_owner_for_update(owner_user_id=owner_user_id)
        preference = self.repository.get_recommendation_preferences_for_update(
            owner_user_id=owner_user_id
        )
        if preference is not None:
            return preference
        preference = RecommendationPreference(
            owner_user_id=owner_user_id,
            default_candidate_limit=_DEFAULT_CANDIDATE_LIMIT,
            question_display_mode=_DEFAULT_QUESTION_DISPLAY_MODE,
            evidence_display_mode=_DEFAULT_EVIDENCE_DISPLAY_MODE,
            show_information_completeness=_DEFAULT_SHOW_INFORMATION_COMPLETENESS,
        )
        self.repository.add(preference)
        self.session.flush()
        return preference

    def update_recommendation_preferences(
        self,
        *,
        owner_user_id: UUID,
        expected_lock_version: int,
        default_candidate_limit: int | None = None,
        question_display_mode: str | None = None,
        evidence_display_mode: str | None = None,
        show_information_completeness: bool | None = None,
    ) -> RecommendationPreference:
        preference = self.get_or_create_recommendation_preferences(owner_user_id=owner_user_id)
        self._require_expected_lock(
            actual_lock_version=preference.lock_version,
            expected_lock_version=expected_lock_version,
            resource="recommendation preferences",
        )
        if default_candidate_limit is not None:
            self._require_positive_int(default_candidate_limit, "candidate limit")
            preference.default_candidate_limit = default_candidate_limit
        if question_display_mode is not None:
            self._require_nonempty(question_display_mode, "question display mode")
            preference.question_display_mode = question_display_mode
        if evidence_display_mode is not None:
            self._require_nonempty(evidence_display_mode, "evidence display mode")
            preference.evidence_display_mode = evidence_display_mode
        if show_information_completeness is not None:
            if not isinstance(show_information_completeness, bool):
                raise PrivacyControlsValidationError(
                    "information completeness display must be boolean"
                )
            preference.show_information_completeness = show_information_completeness
        preference.lock_version += 1
        preference.updated_at = datetime.now(UTC)
        self.session.flush()
        return preference

    def update_project_recommendation_preferences(
        self,
        *,
        owner_user_id: UUID,
        project_id: UUID,
        expected_lock_version: int | None,
        candidate_limit: int | None = None,
        question_display_mode: str | None = None,
        evidence_display_mode: str | None = None,
        show_information_completeness: bool | None = None,
    ) -> ProjectRecommendationPreference:
        project = self.repository.get_project_for_update(
            owner_user_id=owner_user_id, project_id=project_id
        )
        if project is None:
            raise PrivacyControlsNotFoundError("project does not exist for this owner")
        preference = self.repository.get_project_preference_for_update(
            owner_user_id=owner_user_id, project_id=project_id
        )
        if preference is None:
            if expected_lock_version is not None:
                raise PrivacyControlsConflictError(
                    "project preference does not yet have a lock version"
                )
            preference = ProjectRecommendationPreference(
                project_id=project.id,
                owner_user_id=owner_user_id,
                candidate_limit=candidate_limit,
                question_display_mode=question_display_mode,
                evidence_display_mode=evidence_display_mode,
                show_information_completeness=show_information_completeness,
            )
            self._validate_project_preference_values(preference)
            self.repository.add(preference)
            self.session.flush()
            return preference
        if expected_lock_version is None:
            raise PrivacyControlsConflictError("project preference lock version is required")
        self._require_expected_lock(
            actual_lock_version=preference.lock_version,
            expected_lock_version=expected_lock_version,
            resource="project recommendation preferences",
        )
        if candidate_limit is not None:
            self._require_positive_int(candidate_limit, "candidate limit")
            preference.candidate_limit = candidate_limit
        if question_display_mode is not None:
            self._require_nonempty(question_display_mode, "question display mode")
            preference.question_display_mode = question_display_mode
        if evidence_display_mode is not None:
            self._require_nonempty(evidence_display_mode, "evidence display mode")
            preference.evidence_display_mode = evidence_display_mode
        if show_information_completeness is not None:
            if not isinstance(show_information_completeness, bool):
                raise PrivacyControlsValidationError(
                    "information completeness display must be boolean"
                )
            preference.show_information_completeness = show_information_completeness
        preference.lock_version += 1
        preference.updated_at = datetime.now(UTC)
        self.session.flush()
        return preference

    def freeze_snapshot_recommendation_preferences(
        self, *, owner_user_id: UUID, snapshot_id: UUID
    ) -> SnapshotRecommendationPreference:
        snapshot = self.repository.get_snapshot_for_update(
            owner_user_id=owner_user_id, snapshot_id=snapshot_id
        )
        if snapshot is None:
            raise PrivacyControlsNotFoundError("snapshot does not exist for this owner")
        existing = self.repository.get_snapshot_preference(
            owner_user_id=owner_user_id, snapshot_id=snapshot_id
        )
        if existing is not None:
            return existing
        global_preference = self.get_or_create_recommendation_preferences(
            owner_user_id=owner_user_id
        )
        project_preference = self.repository.get_project_preference_for_update(
            owner_user_id=owner_user_id, project_id=snapshot.project_id
        )
        frozen = SnapshotRecommendationPreference(
            snapshot_id=snapshot.id,
            owner_user_id=owner_user_id,
            candidate_limit=(
                project_preference.candidate_limit
                if project_preference is not None and project_preference.candidate_limit is not None
                else global_preference.default_candidate_limit
            ),
            question_display_mode=(
                project_preference.question_display_mode
                if project_preference is not None
                and project_preference.question_display_mode is not None
                else global_preference.question_display_mode
            ),
            evidence_display_mode=(
                project_preference.evidence_display_mode
                if project_preference is not None
                and project_preference.evidence_display_mode is not None
                else global_preference.evidence_display_mode
            ),
            show_information_completeness=(
                project_preference.show_information_completeness
                if project_preference is not None
                and project_preference.show_information_completeness is not None
                else global_preference.show_information_completeness
            ),
            user_preference_lock_version=global_preference.lock_version,
            project_preference_lock_version=(
                project_preference.lock_version if project_preference is not None else None
            ),
        )
        self.repository.add(frozen)
        self.session.flush()
        return frozen

    def record_consent(
        self,
        *,
        owner_user_id: UUID,
        consent_type: str,
        policy_version: str,
        granted: bool,
    ) -> Consent:
        self._require_owner_for_update(owner_user_id=owner_user_id)
        self._require_nonempty(consent_type, "consent type")
        self._require_nonempty(policy_version, "policy version")
        if not isinstance(granted, bool):
            raise PrivacyControlsValidationError("consent choice must be boolean")
        consent = Consent(
            owner_user_id=owner_user_id,
            consent_type=consent_type,
            policy_version=policy_version,
            granted=granted,
        )
        self.repository.add(consent)
        self.session.flush()
        return consent

    def set_retention_preference(
        self,
        *,
        owner_user_id: UUID,
        option_id: str,
        policy_version: str,
        expected_lock_version: int | None,
    ) -> RetentionPreference:
        self._require_owner_for_update(owner_user_id=owner_user_id)
        self._require_nonempty(option_id, "retention option")
        self._require_nonempty(policy_version, "retention policy version")
        preference = self.repository.get_retention_preference_for_update(
            owner_user_id=owner_user_id
        )
        if preference is None:
            if expected_lock_version is not None:
                raise PrivacyControlsConflictError(
                    "retention preference does not yet have a lock version"
                )
            preference = RetentionPreference(
                owner_user_id=owner_user_id,
                option_id=option_id,
                policy_version=policy_version,
            )
            self.repository.add(preference)
        else:
            if expected_lock_version is None:
                raise PrivacyControlsConflictError("retention preference lock version is required")
            self._require_expected_lock(
                actual_lock_version=preference.lock_version,
                expected_lock_version=expected_lock_version,
                resource="retention preference",
            )
            preference.option_id = option_id
            preference.policy_version = policy_version
            preference.lock_version += 1
            preference.updated_at = datetime.now(UTC)
        self.session.flush()
        return preference

    def record_feedback(
        self,
        *,
        owner_user_id: UUID,
        category_l1: str,
        decision_helpfulness: str | None = None,
        category_l2: str | None = None,
        other_text: str | None = None,
        run_id: UUID | None = None,
        snapshot_id: UUID | None = None,
        candidate_id: UUID | None = None,
        missing_activity_id: UUID | None = None,
        missing_episode_id: UUID | None = None,
    ) -> Feedback:
        self._require_owner_for_update(owner_user_id=owner_user_id)
        self._require_nonempty(category_l1, "feedback category")
        if decision_helpfulness not in {None, "HELPFUL", "NOT_HELPFUL", "NOT_SURE"}:
            raise PrivacyControlsValidationError("unknown helpfulness value")
        if missing_activity_id is not None and missing_episode_id is not None:
            raise PrivacyControlsValidationError(
                "only one missing Experience resource may be reported"
            )
        if candidate_id is not None and run_id is None:
            raise PrivacyControlsValidationError(
                "candidate feedback requires its recommendation run"
            )
        if other_text is not None:
            self._require_nonempty(other_text, "feedback other text")
            if len(other_text.encode("utf-8")) > _MAX_OTHER_TEXT_BYTES:
                raise PrivacyControlsValidationError("feedback other text exceeds 4 KiB")
        feedback = Feedback(
            owner_user_id=owner_user_id,
            run_id=run_id,
            snapshot_id=snapshot_id,
            candidate_id=candidate_id,
            decision_helpfulness=decision_helpfulness,
            category_l1=category_l1,
            category_l2=category_l2,
            other_text=other_text,
            missing_activity_id=missing_activity_id,
            missing_episode_id=missing_episode_id,
        )
        self.repository.add(feedback)
        self.session.flush()
        return feedback

    def record_analytics_event(
        self,
        *,
        owner_user_id: UUID,
        event_key: str,
        pseudonymous_subject_id: str,
        event_type: str,
        consent_policy_version: str,
        allowed_properties: dict[str, object],
        project_id: UUID | None = None,
        question_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> AnalyticsEvent:
        self._require_owner_for_update(owner_user_id=owner_user_id)
        self._require_nonempty(event_key, "analytics event key")
        self._require_nonempty(pseudonymous_subject_id, "pseudonymous subject")
        self._require_nonempty(consent_policy_version, "consent policy version")
        if pseudonymous_subject_id == str(owner_user_id):
            raise PrivacyControlsValidationError(
                "analytics subject must not be the owner identifier"
            )
        self._validate_analytics_properties(event_type=event_type, properties=allowed_properties)
        consent = self.repository.get_latest_consent_for_update(
            owner_user_id=owner_user_id, consent_type="ANALYTICS"
        )
        if (
            consent is None
            or not consent.granted
            or consent.policy_version != consent_policy_version
        ):
            raise ExternalProcessingBlockedError("analytics requires current explicit opt-in")
        event = AnalyticsEvent(
            event_key=event_key,
            pseudonymous_subject_id=pseudonymous_subject_id,
            owner_user_id=owner_user_id,
            event_type=event_type,
            project_id=project_id,
            question_id=question_id,
            run_id=run_id,
            consent_policy_version=consent_policy_version,
            allowed_properties=allowed_properties,
        )
        self.repository.add(event)
        self.session.flush()
        return event

    def create_sensitivity_assessment(
        self,
        *,
        owner_user_id: UUID,
        detector_version: str,
        activity_version_id: UUID | None = None,
        episode_version_id: UUID | None = None,
    ) -> SensitivityAssessment:
        if (activity_version_id is None) == (episode_version_id is None):
            raise PrivacyControlsValidationError("exactly one Experience Version is required")
        self._require_nonempty(detector_version, "detector version")
        if (
            activity_version_id is not None
            and self.repository.get_activity_version(
                owner_user_id=owner_user_id, activity_version_id=activity_version_id
            )
            is None
        ):
            raise PrivacyControlsNotFoundError("activity version does not exist for this owner")
        if (
            episode_version_id is not None
            and self.repository.get_episode_version(
                owner_user_id=owner_user_id, episode_version_id=episode_version_id
            )
            is None
        ):
            raise PrivacyControlsNotFoundError("episode version does not exist for this owner")
        assessment = SensitivityAssessment(
            owner_user_id=owner_user_id,
            activity_version_id=activity_version_id,
            episode_version_id=episode_version_id,
            detector_version=detector_version,
            # PostgreSQL ``now()`` is fixed at transaction start.  Explicit wall-clock
            # creation time keeps the latest-assessment gate deterministic when a caller
            # creates more than one assessment in one transaction.
            created_at=datetime.now(UTC),
        )
        self.repository.add(assessment)
        self.session.flush()
        return assessment

    def add_sensitivity_finding(
        self,
        *,
        owner_user_id: UUID,
        assessment_id: UUID,
        category: str,
        field_name: str,
        severity: str,
        source_span_start: int | None = None,
        source_span_end: int | None = None,
    ) -> SensitivityFinding:
        self._require_nonempty(category, "sensitivity category")
        self._require_nonempty(field_name, "sensitivity field name")
        self._require_nonempty(severity, "sensitivity severity")
        self._validate_span(start=source_span_start, end=source_span_end)
        assessment = self._require_assessment_for_update(
            owner_user_id=owner_user_id, assessment_id=assessment_id
        )
        if assessment.status not in {"PENDING", "REVIEW_REQUIRED"}:
            raise PrivacyControlsConflictError(
                "completed sensitivity assessments cannot gain findings"
            )
        finding = SensitivityFinding(
            assessment_id=assessment.id,
            owner_user_id=owner_user_id,
            category=category,
            field_name=field_name,
            severity=severity,
            source_span_start=source_span_start,
            source_span_end=source_span_end,
        )
        self.repository.add(finding)
        assessment.status = "REVIEW_REQUIRED"
        self.session.flush()
        return finding

    def complete_sensitivity_assessment_without_findings(
        self, *, owner_user_id: UUID, assessment_id: UUID
    ) -> SensitivityAssessment:
        assessment = self._require_assessment_for_update(
            owner_user_id=owner_user_id, assessment_id=assessment_id
        )
        if assessment.status != "PENDING" or self.repository.assessment_finding_count(
            assessment_id=assessment.id
        ):
            raise PrivacyControlsConflictError(
                "only a clean pending assessment can complete without review"
            )
        assessment.status = "DECIDED"
        self.session.flush()
        return assessment

    def mark_sensitivity_assessment_failed(
        self, *, owner_user_id: UUID, assessment_id: UUID
    ) -> SensitivityAssessment:
        assessment = self._require_assessment_for_update(
            owner_user_id=owner_user_id, assessment_id=assessment_id
        )
        if assessment.status in {"DECIDED", "FAILED"}:
            raise PrivacyControlsConflictError("completed sensitivity assessment cannot fail")
        assessment.status = "FAILED"
        self.session.flush()
        return assessment

    def record_sensitivity_decision(
        self,
        *,
        owner_user_id: UUID,
        assessment_id: UUID,
        decision: str,
    ) -> SensitivityDecision:
        if decision not in {
            "KEEP",
            "REDACT_BEFORE_EXTERNAL",
            "EXCLUDE_FROM_EXTERNAL",
            "DELETE",
        }:
            raise PrivacyControlsValidationError("unknown sensitivity decision")
        assessment = self._require_assessment_for_update(
            owner_user_id=owner_user_id, assessment_id=assessment_id
        )
        if assessment.status not in {"REVIEW_REQUIRED", "DECIDED"}:
            raise PrivacyControlsConflictError(
                "sensitivity decision requires a completed finding review"
            )
        if not self.repository.assessment_finding_count(assessment_id=assessment.id):
            raise PrivacyControlsConflictError(
                "a clean assessment does not need a sensitivity decision"
            )
        record = SensitivityDecision(
            assessment_id=assessment.id,
            owner_user_id=owner_user_id,
            decision_no=self.repository.next_sensitivity_decision_no(assessment_id=assessment.id),
            decision=decision,
        )
        self.repository.add(record)
        assessment.status = "DECIDED"
        self.session.flush()
        return record

    def external_processing_eligibility(
        self,
        *,
        owner_user_id: UUID,
        activity_version_id: UUID | None = None,
        episode_version_id: UUID | None = None,
    ) -> ExternalProcessingEligibility:
        if (activity_version_id is None) == (episode_version_id is None):
            raise PrivacyControlsValidationError("exactly one Experience Version is required")
        if activity_version_id is not None:
            if (
                self.repository.get_activity_version(
                    owner_user_id=owner_user_id, activity_version_id=activity_version_id
                )
                is None
            ):
                raise PrivacyControlsNotFoundError("activity version does not exist for this owner")
            assessment = self.repository.get_latest_activity_assessment(
                owner_user_id=owner_user_id, activity_version_id=activity_version_id
            )
        else:
            assert episode_version_id is not None
            if (
                self.repository.get_episode_version(
                    owner_user_id=owner_user_id, episode_version_id=episode_version_id
                )
                is None
            ):
                raise PrivacyControlsNotFoundError("episode version does not exist for this owner")
            assessment = self.repository.get_latest_episode_assessment(
                owner_user_id=owner_user_id, episode_version_id=episode_version_id
            )
        if assessment is None:
            return ExternalProcessingEligibility(
                eligible=False,
                mode=None,
                reason_code="SENSITIVITY_ASSESSMENT_REQUIRED",
                assessment_id=None,
                decision_id=None,
            )
        if assessment.status == "FAILED":
            return ExternalProcessingEligibility(
                eligible=False,
                mode=None,
                reason_code="SENSITIVITY_ASSESSMENT_FAILED",
                assessment_id=assessment.id,
                decision_id=None,
            )
        if assessment.status != "DECIDED":
            return ExternalProcessingEligibility(
                eligible=False,
                mode=None,
                reason_code="SENSITIVITY_REVIEW_REQUIRED",
                assessment_id=assessment.id,
                decision_id=None,
            )
        if not self.repository.assessment_finding_count(assessment_id=assessment.id):
            return ExternalProcessingEligibility(
                eligible=True,
                mode="NO_SENSITIVE_FINDINGS",
                reason_code="SENSITIVITY_CLEAN",
                assessment_id=assessment.id,
                decision_id=None,
            )
        decision = self.repository.get_latest_sensitivity_decision(assessment_id=assessment.id)
        if decision is None:
            return ExternalProcessingEligibility(
                eligible=False,
                mode=None,
                reason_code="SENSITIVITY_DECISION_REQUIRED",
                assessment_id=assessment.id,
                decision_id=None,
            )
        if decision.decision == "REDACT_BEFORE_EXTERNAL":
            return ExternalProcessingEligibility(
                eligible=True,
                mode="REDACTED_DERIVATIVE_ONLY",
                reason_code="SENSITIVITY_REDACTION_REQUIRED",
                assessment_id=assessment.id,
                decision_id=decision.id,
            )
        # KEEP preserves the user's local Experience but does not create a blanket
        # authorization to transmit detected content before the G-04 external-data
        # policy is adopted.  This is intentionally fail-closed.
        return ExternalProcessingEligibility(
            eligible=False,
            mode=None,
            reason_code=f"SENSITIVITY_{decision.decision}_BLOCKS_EXTERNAL",
            assessment_id=assessment.id,
            decision_id=decision.id,
        )

    def require_external_processing_eligible(
        self,
        *,
        owner_user_id: UUID,
        activity_version_id: UUID | None = None,
        episode_version_id: UUID | None = None,
    ) -> ExternalProcessingEligibility:
        eligibility = self.external_processing_eligibility(
            owner_user_id=owner_user_id,
            activity_version_id=activity_version_id,
            episode_version_id=episode_version_id,
        )
        if not eligibility.eligible:
            raise ExternalProcessingBlockedError(eligibility.reason_code)
        return eligibility

    def _require_owner_for_update(self, *, owner_user_id: UUID) -> object:
        owner = self.repository.get_owner_for_update(owner_user_id=owner_user_id)
        if owner is None:
            raise PrivacyControlsNotFoundError("owner does not exist")
        return owner

    def _require_assessment_for_update(
        self, *, owner_user_id: UUID, assessment_id: UUID
    ) -> SensitivityAssessment:
        assessment = self.repository.get_assessment_for_update(
            owner_user_id=owner_user_id, assessment_id=assessment_id
        )
        if assessment is None:
            raise PrivacyControlsNotFoundError(
                "sensitivity assessment does not exist for this owner"
            )
        return assessment

    @staticmethod
    def _require_nonempty(value: object, label: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise PrivacyControlsValidationError(f"{label} must be present")

    @staticmethod
    def _require_positive_int(value: object, label: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise PrivacyControlsValidationError(f"{label} must be a positive integer")

    @staticmethod
    def _require_expected_lock(
        *, actual_lock_version: int, expected_lock_version: int, resource: str
    ) -> None:
        if actual_lock_version != expected_lock_version:
            raise PrivacyControlsConflictError(f"{resource} has a newer version")

    @classmethod
    def _validate_json_object(cls, value: object, *, label: str, maximum_bytes: int) -> None:
        if not isinstance(value, dict):
            raise PrivacyControlsValidationError(f"{label} must be a JSON object")
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise PrivacyControlsValidationError(f"{label} must be JSON serializable") from error
        if len(encoded) > maximum_bytes:
            raise PrivacyControlsValidationError(f"{label} exceeds its safe size limit")

    @classmethod
    def _validate_analytics_properties(
        cls, *, event_type: str, properties: dict[str, object]
    ) -> None:
        allowed_keys = _ANALYTICS_EVENT_PROPERTIES.get(event_type)
        if allowed_keys is None:
            raise PrivacyControlsValidationError("analytics event type is not registered")
        cls._validate_json_object(
            properties, label="analytics properties", maximum_bytes=_MAX_DISPLAY_OPTIONS_BYTES
        )
        if set(properties) != allowed_keys:
            raise PrivacyControlsValidationError(
                "analytics properties do not match the event registry"
            )
        for key, value in properties.items():
            if not isinstance(value, str):
                raise PrivacyControlsValidationError(
                    "analytics property values must be opaque UUIDs"
                )
            try:
                UUID(value)
            except ValueError as error:
                raise PrivacyControlsValidationError(
                    f"analytics property {key} must be an opaque UUID"
                ) from error

    @classmethod
    def _validate_project_preference_values(
        cls, preference: ProjectRecommendationPreference
    ) -> None:
        if preference.candidate_limit is not None:
            cls._require_positive_int(preference.candidate_limit, "candidate limit")
        if preference.question_display_mode is not None:
            cls._require_nonempty(preference.question_display_mode, "question display mode")
        if preference.evidence_display_mode is not None:
            cls._require_nonempty(preference.evidence_display_mode, "evidence display mode")
        if preference.show_information_completeness is not None and not isinstance(
            preference.show_information_completeness, bool
        ):
            raise PrivacyControlsValidationError("information completeness display must be boolean")

    @staticmethod
    def _validate_span(*, start: int | None, end: int | None) -> None:
        if start is not None and (not isinstance(start, int) or start < 0):
            raise PrivacyControlsValidationError("sensitivity span start cannot be negative")
        if end is not None and (not isinstance(end, int) or end < 0):
            raise PrivacyControlsValidationError("sensitivity span end cannot be negative")
        if start is not None and end is not None and end < start:
            raise PrivacyControlsValidationError("sensitivity span end cannot precede its start")
