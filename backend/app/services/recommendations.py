from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.projection import SnapshotExclusion
from app.models.recommendations import (
    MaterialSelectionItem,
    MaterialSelectionSet,
    ProjectSnapshot,
    RecommendationCandidate,
    RecommendationRun,
    SnapshotEpisodeVersion,
)
from app.repo.recommendations import RecommendationRepository

_MATCH_STATUSES = {
    "DIRECT_MATCH",
    "PARTIAL_RELEVANCE",
    "NEEDS_VERIFICATION",
    "NO_RELEVANT_EVIDENCE",
}
_VALIDATION_STATUSES = {"PENDING", "PASSED", "LIMITED", "FAILED"}


class RecommendationError(Exception):
    pass


class RecommendationNotFoundError(RecommendationError):
    pass


class RecommendationValidationError(RecommendationError):
    pass


class RecommendationService:
    """Transaction-scoped Snapshot, candidate, and material-selection orchestration.

    This service persists a synthetic recommendation flow only. It deliberately has no W3 ACK,
    W4 transport, or Job completion side effect; those remain additive integration work.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = RecommendationRepository(session)

    def create_snapshot(
        self,
        *,
        owner_user_id: UUID,
        project_id: UUID,
        episode_version_ids: Sequence[UUID] = (),
        recommendation_policy_version: str | None = None,
        limitations: Sequence[str] = (),
    ) -> ProjectSnapshot:
        project = self.repository.get_project_for_update(
            project_id=project_id, owner_user_id=owner_user_id
        )
        if project is None or project.current_version_id is None:
            raise RecommendationNotFoundError("project does not exist for this owner")
        project_version = self.repository.get_project_version(
            project_version_id=project.current_version_id,
            project_id=project.id,
            owner_user_id=owner_user_id,
        )
        if project_version is None:
            raise RecommendationNotFoundError("project version does not exist for this owner")
        episode_ids = tuple(episode_version_ids)
        self._require_unique_ids(episode_ids, "episode version")
        episode_versions = self.repository.get_episode_versions(
            episode_version_ids=episode_ids, owner_user_id=owner_user_id
        )
        if len(episode_versions) != len(episode_ids):
            raise RecommendationNotFoundError("an episode version does not exist for this owner")

        snapshot = ProjectSnapshot(
            owner_user_id=owner_user_id,
            project_id=project.id,
            project_version_id=project.current_version_id,
            snapshot_no=self.repository.next_snapshot_no(project_id=project.id),
            recommendation_policy_version=recommendation_policy_version,
            limitations=self._normalize_limitations(limitations),
        )
        self.repository.add_snapshot(snapshot)
        self.session.flush()
        for episode_version in episode_versions:
            self.repository.add_snapshot_episode_version(
                SnapshotEpisodeVersion(
                    snapshot_id=snapshot.id,
                    episode_version_id=episode_version.id,
                    owner_user_id=owner_user_id,
                )
            )
        self.session.flush()
        for exclusion in self.repository.get_active_exclusions_for_episode_versions(
            owner_user_id=owner_user_id,
            episode_version_ids=episode_ids,
            project_id=project.id,
            company_id=project_version.company_id,
            role_id=self.repository.get_role_id_for_role_version(
                role_version_id=project_version.role_version_id
            ),
        ):
            self.repository.add_snapshot_exclusion(
                SnapshotExclusion(
                    snapshot_id=snapshot.id,
                    exclusion_id=exclusion.id,
                    owner_user_id=owner_user_id,
                )
            )
        self.session.flush()
        # Preferences are resolved once here and remain immutable beneath this
        # Snapshot. Later user/project preference edits must not reinterpret an
        # existing recommendation input.
        from app.services.privacy_controls import PrivacyControlsService

        PrivacyControlsService(self.session).freeze_snapshot_recommendation_preferences(
            owner_user_id=owner_user_id,
            snapshot_id=snapshot.id,
        )
        snapshot.status = "READY"
        project.active_snapshot_id = snapshot.id
        self.session.flush()
        return snapshot

    def create_recommendation_run(
        self,
        *,
        owner_user_id: UUID,
        project_id: UUID,
        question_id: UUID,
        snapshot_id: UUID,
        analysis_policy_version: str,
        requested_candidate_limit: int,
        question_version_id: UUID | None = None,
        analysis_input_version: str | None = None,
        job_id: UUID | None = None,
        limitations: Sequence[str] = (),
    ) -> RecommendationRun:
        self._require_nonempty(analysis_policy_version, "analysis policy version")
        if not isinstance(requested_candidate_limit, int) or requested_candidate_limit <= 0:
            raise RecommendationValidationError("requested candidate limit must be positive")
        project = self.repository.get_project_for_update(
            project_id=project_id, owner_user_id=owner_user_id
        )
        if project is None:
            raise RecommendationNotFoundError("project does not exist for this owner")
        question = self.repository.get_question_for_update(
            question_id=question_id, owner_user_id=owner_user_id
        )
        if (
            question is None
            or question.project_id != project.id
            or question.current_version_id is None
        ):
            raise RecommendationNotFoundError(
                "question does not exist in this project for this owner"
            )
        selected_question_version_id = question_version_id or question.current_version_id
        question_version = self.repository.get_question_version(
            question_version_id=selected_question_version_id, owner_user_id=owner_user_id
        )
        if question_version is None or question_version.question_id != question.id:
            raise RecommendationNotFoundError("question version does not belong to this question")
        snapshot = self.repository.get_snapshot(
            snapshot_id=snapshot_id, owner_user_id=owner_user_id
        )
        if snapshot is None or snapshot.project_id != project.id:
            raise RecommendationNotFoundError(
                "snapshot does not belong to this project for this owner"
            )
        if snapshot.status != "READY":
            raise RecommendationValidationError(
                "only a ready snapshot can start a recommendation run"
            )

        run = RecommendationRun(
            owner_user_id=owner_user_id,
            project_id=project.id,
            question_id=question.id,
            question_version_id=question_version.id,
            snapshot_id=snapshot.id,
            job_id=job_id,
            analysis_policy_version=analysis_policy_version,
            analysis_input_version=analysis_input_version,
            result_origin="SYNTHETIC",
            requested_candidate_limit=requested_candidate_limit,
            limitations=self._normalize_limitations(limitations),
        )
        self.repository.add_run(run)
        project.status = "RECOMMENDING"
        self.session.flush()
        return run

    def record_candidate(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
        episode_version_id: UUID,
        match_status: str,
        short_reason: str,
        result_version: str,
        validation_status: str = "PENDING",
        strength_summary: str | None = None,
        limitation_summary: str | None = None,
        internal_rank: int | None = None,
    ) -> RecommendationCandidate:
        if match_status not in _MATCH_STATUSES:
            raise RecommendationValidationError("unknown candidate match status")
        if validation_status not in _VALIDATION_STATUSES:
            raise RecommendationValidationError("unknown candidate validation status")
        self._require_nonempty(short_reason, "candidate short reason")
        self._require_nonempty(result_version, "candidate result version")
        if internal_rank is not None and (not isinstance(internal_rank, int) or internal_rank <= 0):
            raise RecommendationValidationError("candidate internal rank must be positive")
        run = self.repository.get_run_for_update(run_id=run_id, owner_user_id=owner_user_id)
        if run is None:
            raise RecommendationNotFoundError("recommendation run does not exist for this owner")
        if not self.repository.snapshot_contains_episode_version(
            snapshot_id=run.snapshot_id,
            episode_version_id=episode_version_id,
            owner_user_id=owner_user_id,
        ):
            raise RecommendationValidationError(
                "candidate episode version is not fixed in the recommendation snapshot"
            )
        candidate = RecommendationCandidate(
            owner_user_id=owner_user_id,
            run_id=run.id,
            question_id=run.question_id,
            snapshot_id=run.snapshot_id,
            candidate_no=self.repository.next_candidate_no(run_id=run.id),
            episode_version_id=episode_version_id,
            match_status=match_status,
            short_reason=short_reason,
            strength_summary=strength_summary,
            limitation_summary=limitation_summary,
            internal_rank=internal_rank,
            validation_status=validation_status,
            result_version=result_version,
        )
        self.repository.add_candidate(candidate)
        self.session.flush()
        return candidate

    def replace_material_selection(
        self,
        *,
        owner_user_id: UUID,
        project_id: UUID,
        question_id: UUID,
        run_id: UUID,
        candidate_ids: Sequence[UUID],
    ) -> MaterialSelectionSet:
        selected_candidate_ids = tuple(candidate_ids)
        if not selected_candidate_ids:
            raise RecommendationValidationError(
                "a material selection must include at least one candidate"
            )
        self._require_unique_ids(selected_candidate_ids, "candidate")
        project = self.repository.get_project_for_update(
            project_id=project_id, owner_user_id=owner_user_id
        )
        if project is None:
            raise RecommendationNotFoundError("project does not exist for this owner")
        question = self.repository.get_question_for_update(
            question_id=question_id, owner_user_id=owner_user_id
        )
        if question is None or question.project_id != project.id:
            raise RecommendationNotFoundError(
                "question does not belong to this project for this owner"
            )
        run = self.repository.get_run_for_update(run_id=run_id, owner_user_id=owner_user_id)
        if run is None or run.question_id != question.id:
            raise RecommendationNotFoundError("run does not belong to this question for this owner")
        candidates = self.repository.get_candidates(
            candidate_ids=selected_candidate_ids,
            run_id=run.id,
            question_id=question.id,
            owner_user_id=owner_user_id,
        )
        if len(candidates) != len(selected_candidate_ids):
            raise RecommendationNotFoundError("a selected candidate does not belong to this run")

        current = self.repository.get_current_selection_for_update(
            question_id=question.id, owner_user_id=owner_user_id
        )
        selection_set = MaterialSelectionSet(
            owner_user_id=owner_user_id,
            question_id=question.id,
            run_id=run.id,
            is_current=False,
        )
        self.repository.add_selection_set(selection_set)
        self.session.flush()
        for selection_order, candidate_id in enumerate(selected_candidate_ids, start=1):
            self.repository.add_selection_item(
                MaterialSelectionItem(
                    selection_set_id=selection_set.id,
                    owner_user_id=owner_user_id,
                    question_id=question.id,
                    run_id=run.id,
                    candidate_id=candidate_id,
                    selection_order=selection_order,
                )
            )
        self.session.flush()
        if current is not None:
            current.is_current = False
            current.superseded_at = datetime.now(UTC)
            # PostgreSQL's partial unique index requires the old current Set to be persisted
            # before the replacement can become current. Both writes remain in the caller's
            # surrounding transaction, so observers never see a committed gap.
            self.session.flush()
        selection_set.is_current = True
        self.session.flush()
        if self.repository.current_selection_count(
            project_id=project.id, owner_user_id=owner_user_id
        ) == self.repository.active_question_count(
            project_id=project.id, owner_user_id=owner_user_id
        ):
            project.status = "MATERIALS_SELECTED"
        self.session.flush()
        return selection_set

    @staticmethod
    def _normalize_limitations(limitations: Sequence[str]) -> list[str]:
        if isinstance(limitations, str) or any(
            not isinstance(limitation, str) or not limitation.strip() for limitation in limitations
        ):
            raise RecommendationValidationError("limitations must be non-empty code strings")
        return list(limitations)

    @staticmethod
    def _require_nonempty(value: object, label: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise RecommendationValidationError(f"{label} must be present")

    @staticmethod
    def _require_unique_ids(values: Sequence[UUID], label: str) -> None:
        if len(set(values)) != len(values):
            raise RecommendationValidationError(f"duplicate {label} identifiers are not allowed")
