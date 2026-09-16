from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.application_workspace import ApplicationProject
from app.models.experience import ActivityVersion, EpisodeVersion
from app.models.identity import User
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
from app.models.recommendations import ProjectSnapshot


class PrivacyControlsRepository:
    """Owner-scoped persistence primitives for settings and privacy gates."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, record: object) -> None:
        self.session.add(record)

    def get_owner_for_update(self, *, owner_user_id: UUID) -> User | None:
        return self.session.scalar(select(User).where(User.id == owner_user_id).with_for_update())

    def get_user_settings_for_update(self, *, owner_user_id: UUID) -> UserSettings | None:
        return self.session.scalar(
            select(UserSettings)
            .where(UserSettings.owner_user_id == owner_user_id)
            .with_for_update()
        )

    def get_user_settings(self, *, owner_user_id: UUID) -> UserSettings | None:
        return self.session.scalar(
            select(UserSettings).where(UserSettings.owner_user_id == owner_user_id)
        )

    def get_recommendation_preferences_for_update(
        self, *, owner_user_id: UUID
    ) -> RecommendationPreference | None:
        return self.session.scalar(
            select(RecommendationPreference)
            .where(RecommendationPreference.owner_user_id == owner_user_id)
            .with_for_update()
        )

    def get_recommendation_preferences(
        self, *, owner_user_id: UUID
    ) -> RecommendationPreference | None:
        return self.session.scalar(
            select(RecommendationPreference).where(
                RecommendationPreference.owner_user_id == owner_user_id
            )
        )

    def get_project_preference_for_update(
        self, *, owner_user_id: UUID, project_id: UUID
    ) -> ProjectRecommendationPreference | None:
        return self.session.scalar(
            select(ProjectRecommendationPreference)
            .where(
                ProjectRecommendationPreference.project_id == project_id,
                ProjectRecommendationPreference.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

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

    def get_snapshot_for_update(
        self, *, owner_user_id: UUID, snapshot_id: UUID
    ) -> ProjectSnapshot | None:
        return self.session.scalar(
            select(ProjectSnapshot)
            .where(
                ProjectSnapshot.id == snapshot_id,
                ProjectSnapshot.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_snapshot_preference(
        self, *, owner_user_id: UUID, snapshot_id: UUID
    ) -> SnapshotRecommendationPreference | None:
        return self.session.scalar(
            select(SnapshotRecommendationPreference).where(
                SnapshotRecommendationPreference.snapshot_id == snapshot_id,
                SnapshotRecommendationPreference.owner_user_id == owner_user_id,
            )
        )

    def get_retention_preference_for_update(
        self, *, owner_user_id: UUID
    ) -> RetentionPreference | None:
        return self.session.scalar(
            select(RetentionPreference)
            .where(RetentionPreference.owner_user_id == owner_user_id)
            .with_for_update()
        )

    def get_latest_consent_for_update(
        self, *, owner_user_id: UUID, consent_type: str
    ) -> Consent | None:
        return self.session.scalar(
            select(Consent)
            .where(Consent.owner_user_id == owner_user_id, Consent.consent_type == consent_type)
            .order_by(Consent.decided_at.desc(), Consent.id.desc())
            .limit(1)
            .with_for_update()
        )

    def get_latest_consent(self, *, owner_user_id: UUID, consent_type: str) -> Consent | None:
        return self.session.scalar(
            select(Consent)
            .where(Consent.owner_user_id == owner_user_id, Consent.consent_type == consent_type)
            .order_by(Consent.decided_at.desc(), Consent.id.desc())
            .limit(1)
        )

    def get_activity_version(
        self, *, owner_user_id: UUID, activity_version_id: UUID
    ) -> ActivityVersion | None:
        return self.session.scalar(
            select(ActivityVersion).where(
                ActivityVersion.id == activity_version_id,
                ActivityVersion.owner_user_id == owner_user_id,
            )
        )

    def get_episode_version(
        self, *, owner_user_id: UUID, episode_version_id: UUID
    ) -> EpisodeVersion | None:
        return self.session.scalar(
            select(EpisodeVersion).where(
                EpisodeVersion.id == episode_version_id,
                EpisodeVersion.owner_user_id == owner_user_id,
            )
        )

    def get_assessment_for_update(
        self, *, owner_user_id: UUID, assessment_id: UUID
    ) -> SensitivityAssessment | None:
        return self.session.scalar(
            select(SensitivityAssessment)
            .where(
                SensitivityAssessment.id == assessment_id,
                SensitivityAssessment.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_latest_activity_assessment(
        self, *, owner_user_id: UUID, activity_version_id: UUID
    ) -> SensitivityAssessment | None:
        return self.session.scalar(
            select(SensitivityAssessment)
            .where(
                SensitivityAssessment.owner_user_id == owner_user_id,
                SensitivityAssessment.activity_version_id == activity_version_id,
            )
            .order_by(SensitivityAssessment.created_at.desc(), SensitivityAssessment.id.desc())
            .limit(1)
        )

    def get_latest_episode_assessment(
        self, *, owner_user_id: UUID, episode_version_id: UUID
    ) -> SensitivityAssessment | None:
        return self.session.scalar(
            select(SensitivityAssessment)
            .where(
                SensitivityAssessment.owner_user_id == owner_user_id,
                SensitivityAssessment.episode_version_id == episode_version_id,
            )
            .order_by(SensitivityAssessment.created_at.desc(), SensitivityAssessment.id.desc())
            .limit(1)
        )

    def assessment_finding_count(self, *, assessment_id: UUID) -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(SensitivityFinding)
                .where(SensitivityFinding.assessment_id == assessment_id)
            )
            or 0
        )

    def next_sensitivity_decision_no(self, *, assessment_id: UUID) -> int:
        last = self.session.scalar(
            select(func.max(SensitivityDecision.decision_no)).where(
                SensitivityDecision.assessment_id == assessment_id
            )
        )
        return int(last or 0) + 1

    def get_latest_sensitivity_decision(self, *, assessment_id: UUID) -> SensitivityDecision | None:
        return self.session.scalar(
            select(SensitivityDecision)
            .where(SensitivityDecision.assessment_id == assessment_id)
            .order_by(SensitivityDecision.decision_no.desc())
            .limit(1)
        )

    def get_feedback(self, *, feedback_id: UUID, owner_user_id: UUID) -> Feedback | None:
        return self.session.scalar(
            select(Feedback).where(
                Feedback.id == feedback_id, Feedback.owner_user_id == owner_user_id
            )
        )

    def get_analytics_event(self, *, event_id: UUID, owner_user_id: UUID) -> AnalyticsEvent | None:
        return self.session.scalar(
            select(AnalyticsEvent).where(
                AnalyticsEvent.id == event_id, AnalyticsEvent.owner_user_id == owner_user_id
            )
        )
