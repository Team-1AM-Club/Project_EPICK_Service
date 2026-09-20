from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.experience import EpisodeVersion
from app.models.identity import User
from app.models.jobs import OutboxMessage
from app.models.projection import SnapshotExclusion
from app.models.recommendation_execution import (
    RecommendationExecutionBinding,
    RecommendationExecutionEpisode,
)
from app.models.recommendations import (
    MaterialSelectionItem,
    MaterialSelectionSet,
    ProjectSnapshot,
    RecommendationCandidate,
    RecommendationRun,
    SnapshotEpisodeVersion,
)
from app.models.sources import Source, SourceVersion
from app.repo.recommendations import RecommendationRepository
from app.services.w4_recommendation_run_store import canonical_sha256

_MATCH_STATUSES = {
    "DIRECT_MATCH",
    "PARTIAL_RELEVANCE",
    "NEEDS_VERIFICATION",
    "NO_RELEVANT_EVIDENCE",
}
_VALIDATION_STATUSES = {"PENDING", "PASSED", "LIMITED", "FAILED"}
W4_SYNTHETIC_QUESTION_SCOPE_ID = "skhynix-2026-march-R260521-IT"


class RecommendationError(Exception):
    pass


class RecommendationNotFoundError(RecommendationError):
    pass


class RecommendationValidationError(RecommendationError):
    pass


class RecommendationStaleInputError(RecommendationError):
    pass


class MaterialSelectionConflictError(RecommendationError):
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

    def create_synthetic_recommendation_run(
        self,
        *,
        owner_user_id: UUID,
        question_id: UUID,
        expected_question_version: int,
        expected_snapshot_no: int,
        requested_candidate_limit: int,
        include_excluded: bool,
        allow_limited_analysis: bool,
    ) -> RecommendationRun:
        """Freeze owner-scoped inputs and accept one synthetic recommendation Run.

        The method intentionally only records a pending Run.  Calling a
        recommendation executor is a separate worker/test seam, never part of
        the HTTP request transaction.
        """

        question = self.repository.get_question_for_update(
            question_id=question_id, owner_user_id=owner_user_id
        )
        if question is None or question.current_version_id is None or question.status != "ACTIVE":
            raise RecommendationNotFoundError("question does not exist for this owner")
        if question.current_version_id is None:
            raise RecommendationNotFoundError("question has no current version")
        question_version = self.repository.get_question_version(
            question_version_id=question.current_version_id,
            owner_user_id=owner_user_id,
        )
        if question_version is None:
            raise RecommendationNotFoundError("question version does not exist for this owner")
        if question_version.version_no != expected_question_version:
            raise RecommendationStaleInputError("question version is stale")

        project = self.repository.get_project_for_update(
            project_id=question.project_id, owner_user_id=owner_user_id
        )
        if project is None or project.current_version_id is None:
            raise RecommendationNotFoundError("project does not exist for this owner")
        active_snapshot_no = 0
        if project.active_snapshot_id is not None:
            active_snapshot = self.repository.get_snapshot(
                snapshot_id=project.active_snapshot_id, owner_user_id=owner_user_id
            )
            if active_snapshot is None:
                raise RecommendationNotFoundError("active snapshot does not exist for this owner")
            active_snapshot_no = active_snapshot.snapshot_no
        if active_snapshot_no != expected_snapshot_no:
            raise RecommendationStaleInputError("snapshot version is stale")

        project_version = self.repository.get_project_version(
            project_version_id=project.current_version_id,
            project_id=project.id,
            owner_user_id=owner_user_id,
        )
        if project_version is None:
            raise RecommendationNotFoundError("project version does not exist for this owner")
        eligible_versions = self.repository.list_current_eligible_episode_versions(
            owner_user_id=owner_user_id
        )
        exclusions = self.repository.get_active_exclusions_for_episode_versions(
            owner_user_id=owner_user_id,
            episode_version_ids=[version.id for version in eligible_versions],
            project_id=project.id,
            company_id=project_version.company_id,
            role_id=self.repository.get_role_id_for_role_version(
                role_version_id=project_version.role_version_id
            ),
        )
        excluded_episode_ids = {
            exclusion.episode_id for exclusion in exclusions if exclusion.episode_id is not None
        }
        excluded_activity_ids = {
            exclusion.activity_id for exclusion in exclusions if exclusion.activity_id is not None
        }
        episode_version_ids = tuple(
            version.id
            for version in eligible_versions
            if include_excluded
            or (
                version.episode_id not in excluded_episode_ids
                and version.activity_id not in excluded_activity_ids
            )
        )
        limitations: list[str] = []
        if not episode_version_ids:
            if not allow_limited_analysis:
                raise RecommendationValidationError("no eligible episode version is available")
            limitations.append("NO_ELIGIBLE_EPISODE_VERSION")

        snapshot = self.create_snapshot(
            owner_user_id=owner_user_id,
            project_id=project.id,
            episode_version_ids=episode_version_ids,
            recommendation_policy_version="synthetic-v1",
            limitations=limitations,
        )
        run = self.create_recommendation_run(
            owner_user_id=owner_user_id,
            project_id=project.id,
            question_id=question.id,
            snapshot_id=snapshot.id,
            analysis_policy_version="synthetic-v1",
            analysis_input_version=f"snapshot:{snapshot.snapshot_no}",
            requested_candidate_limit=requested_candidate_limit,
            question_version_id=question_version.id,
            limitations=limitations,
        )
        run.limited_analysis = bool(limitations)
        self.session.flush()
        return run

    def create_server_selected_recommendation_run(
        self,
        *,
        owner_user_id: UUID,
        question_id: UUID,
        expected_question_version: int,
        expected_snapshot_no: int,
        requested_candidate_limit: int,
        include_excluded: bool,
        allow_limited_analysis: bool,
    ) -> RecommendationRun:
        """Accept the public DTO while deployment policy selects the executor.

        ENGINE creates only durable W1 state and an opaque outbox trigger. It
        never invokes W4 or falls back to the synthetic adapter in this request.
        """

        run = self.create_synthetic_recommendation_run(
            owner_user_id=owner_user_id,
            question_id=question_id,
            expected_question_version=expected_question_version,
            expected_snapshot_no=expected_snapshot_no,
            requested_candidate_limit=requested_candidate_limit,
            include_excluded=include_excluded,
            allow_limited_analysis=allow_limited_analysis,
        )
        if settings.w1_recommendation_execution_mode == "SYNTHETIC":
            return run
        self._adopt_engine_execution(owner_user_id=owner_user_id, run=run)
        return run

    def _adopt_engine_execution(
        self,
        *,
        owner_user_id: UUID,
        run: RecommendationRun,
    ) -> None:
        owner = self.session.get(User, owner_user_id)
        if owner is None or owner.account_status != "ACTIVE" or owner.deleted_at is not None:
            raise RecommendationNotFoundError("owner is not active")
        question_version = self.repository.get_question_version(
            question_version_id=run.question_version_id,
            owner_user_id=owner_user_id,
        )
        episode_versions = self.repository.get_episode_versions(
            episode_version_ids=self.repository.list_snapshot_episode_version_ids(
                snapshot_id=run.snapshot_id,
                owner_user_id=owner_user_id,
            ),
            owner_user_id=owner_user_id,
        )
        source = self.session.scalar(
            select(Source)
            .where(Source.current_version_id.is_not(None))
            .order_by(Source.id)
            .limit(1)
        )
        source_version = (
            self.session.get(SourceVersion, source.current_version_id)
            if source is not None and source.current_version_id is not None
            else None
        )
        if question_version is None or source is None or source_version is None:
            raise RecommendationValidationError(
                "ENGINE synthetic acceptance requires a current question and Source version"
            )
        context_version = f"w1-engine-{run.id}"
        # The delivered W4 acceptance runtime is intentionally pinned to its
        # reviewed draft criteria catalog. Arbitrary question scopes and user
        # themes remain outside Stage 4.5 while REAL input is disabled.
        question_scope_id = W4_SYNTHETIC_QUESTION_SCOPE_ID
        context = self._engine_context(
            run=run,
            owner_user_id=owner_user_id,
            question_scope_id=question_scope_id,
            context_version=context_version,
            episode_versions=episode_versions,
            source=source,
            source_version=source_version,
        )
        request = {
            "schema_version": "w4-service-input/0.1",
            "request_id": str(run.id),
            "project_id": str(run.project_id),
            "question": {
                "scope_id": question_scope_id,
                "question_id": "job_experience",
                "user_theme": None,
            },
            "top_k": run.requested_candidate_limit,
        }
        run.result_origin = "ENGINE"
        run.analysis_policy_version = "w4-synthetic-acceptance-v1"
        run.limited_analysis = True
        run.limitations = ["W4_SYNTHETIC_ACCEPTANCE_ONLY"]
        binding = RecommendationExecutionBinding(
            run_id=run.id,
            owner_user_id=owner_user_id,
            project_id=run.project_id,
            question_id=run.question_id,
            question_version_id=run.question_version_id,
            snapshot_id=run.snapshot_id,
            owner_deletion_epoch=owner.deletion_epoch,
            context_sha256=canonical_sha256(context),
            contract_version="w1-w4-recommendation/1.0",
            engine_source_revision=settings.w4_recommendation_engine_source_revision,
            request_body=request,
            context_body=context,
        )
        self.session.add(binding)
        self.session.flush()
        for episode in episode_versions:
            self.session.add(
                RecommendationExecutionEpisode(
                    binding_id=binding.id,
                    episode_version_id=episode.id,
                    owner_user_id=owner_user_id,
                    episode_id=str(episode.episode_id),
                    episode_version=episode.version_no,
                )
            )
        self.session.add(
            OutboxMessage(
                message_type="w1.private.w4.recommendation-execution.v1",
                schema_version="w1.w4.recommendation-exec/1",
                visibility_scope="PRIVATE",
                aggregate_type="recommendation_run",
                aggregate_id=run.id,
                aggregate_revision=1,
                recommendation_run_id=run.id,
                owner_user_id=owner_user_id,
                owner_deletion_epoch=owner.deletion_epoch,
                payload={
                    "run_id": str(run.id),
                    "binding_id": str(binding.id),
                    "contract_version": binding.contract_version,
                    "engine_source_revision": binding.engine_source_revision,
                    "schema_manifest_sha256": (settings.w4_recommendation_schema_manifest_sha256),
                },
            )
        )
        self.session.flush()

    @staticmethod
    def _engine_context(
        *,
        run: RecommendationRun,
        owner_user_id: UUID,
        question_scope_id: str,
        context_version: str,
        episode_versions: Sequence[EpisodeVersion],
        source: Source,
        source_version: SourceVersion,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        index_key = {
            "source_version_id": str(source_version.id),
            "extraction_revision_id": str(source_version.id),
            "representation": "w1-source-version",
            "normalization_version": source_version.content_normalization_version,
        }
        source_ref = {
            "source_id": str(source.id),
            "source_version_id": str(source_version.id),
            "source_kind": source.source_type.lower(),
        }
        signal = {
            "schema_version": "w3-c01/0.2-candidate",
            "source_id": str(source.id),
            "event_cursor": source_version.version_no,
            "required_event_cursor": source_version.version_no,
            "restriction_revision": source_version.version_no,
            "required_restriction_revision": source_version.version_no,
            "generation": source_version.version_no,
            "restriction_scope": "version",
            "reason": "READY",
            "index_ack": True,
            "index_key": index_key,
            "history_complete": True,
            "event_type": "w3.source.usability.changed",
            "signal_id": str(source_version.id),
            "usable": True,
        }
        synthetic_excerpt = (
            "T104 synthetic acceptance source; no production or user-provided content."
        )
        synthetic_limitation = {
            "code": "W4_SYNTHETIC_ACCEPTANCE_ONLY",
            "impact": "Synthetic acceptance does not prove REAL-data readiness.",
            "source_ref": source_ref,
            "artifact_id": f"t104-synthetic-{source_version.id}",
        }
        knowledge = {
            "status": "LIMITED",
            "errors": [],
            "bundle": {
                "mode": "SYNTHETIC",
                "purpose": "SYNTHETIC_ACCEPTANCE",
                "requirement_presence": "NOT_ASSESSED",
                "evidences": [
                    {
                        "evidence_id": "t104-synthetic-evidence",
                        "artifact_id": f"t104-synthetic-{source_version.id}",
                        "source_ref": source_ref,
                        "excerpt": synthetic_excerpt,
                        "native_locator": {
                            "locator_type": "external_reference",
                            "value": f"synthetic:t104:{source_version.id}",
                            "reproducible": True,
                        },
                        "observed_integrity": {
                            "algorithm": "sha256",
                            "digest": hashlib.sha256(synthetic_excerpt.encode()).hexdigest(),
                            "scope": "excerpt",
                            "reported_by": "W3_CURRENT",
                        },
                    }
                ],
                "claims": [],
                "requirements": [],
                "source_reviews": [
                    {
                        "source_ref": source_ref,
                        "checks": [],
                        "limitations": [synthetic_limitation],
                        "process_status": "LIMITED",
                    }
                ],
                "limitations": [synthetic_limitation],
            },
        }
        episodes = []
        for episode in episode_versions:
            raw_text = episode.original_narrative or "\n".join(
                value
                for value in (
                    episode.situation_text,
                    episode.problem_text,
                    episode.goal_text,
                    episode.actions_text,
                    episode.result_text,
                    episode.learning_text,
                )
                if value
            )
            episodes.append(
                {
                    "episode_id": str(episode.episode_id),
                    "version": episode.version_no,
                    "owner_id": str(owner_user_id),
                    "activity_id": str(episode.activity_id),
                    "title": episode.title,
                    "raw_text": raw_text or "합성 검증용 빈 경험입니다.",
                }
            )
        return {
            "schema_version": "w4-server-context/0.2",
            "context_version": context_version,
            "data_kind": "SYNTHETIC",
            "project": {"owner_id": str(owner_user_id), "project_id": str(run.project_id)},
            "snapshot": {
                "snapshot_id": str(run.snapshot_id),
                "episode_versions": {
                    str(item.episode_id): item.version_no for item in episode_versions
                },
            },
            "episodes": episodes,
            "excluded_episode_ids": [],
            "question_scope_id": question_scope_id,
            "company_knowledge": {
                "schema_version": "w4-c01-knowledge/0.1-proposal",
                "profile": "w3-c01/0.2-candidate/r2",
                "upstream_commit": "05f26b4c0a52aecf155a66df0fcaa38e6fbd4cf5",
                "knowledge_bundle_id": f"w1-synthetic-{run.id}",
                "data_kind": "SYNTHETIC",
                "scope_id": question_scope_id,
                "as_of": now.date().isoformat(),
                "sources": [
                    {
                        "signal": signal,
                        "knowledge_generation": source_version.version_no,
                        "knowledge": knowledge,
                        "metadata": {
                            "source_id": str(source.id),
                            "index_key": index_key,
                            "scope_id": question_scope_id,
                            "content_sha256": source_version.content_hash,
                            "published_at": None,
                            "valid_from": None,
                            "valid_to": None,
                            "parse_status": "PARSED",
                            "required_for_scope": True,
                            "expires_at": (now + timedelta(minutes=30))
                            .isoformat()
                            .replace("+00:00", "Z"),
                        },
                    }
                ],
            },
        }

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

    def select_candidate(
        self,
        *,
        owner_user_id: UUID,
        candidate_id: UUID,
        question_id: UUID,
        run_id: UUID,
        result_version: str,
        replace_existing: bool,
    ) -> tuple[MaterialSelectionSet, bool]:
        """Select one candidate and return ``(selection, created)``.

        A duplicate current selection is a domain-level idempotent result even
        when the caller uses a new HTTP idempotency key.
        """

        candidate = self.repository.get_candidate(
            candidate_id=candidate_id, owner_user_id=owner_user_id
        )
        if candidate is None or candidate.run_id != run_id or candidate.question_id != question_id:
            raise RecommendationNotFoundError("candidate does not belong to this run or question")
        if candidate.result_version != result_version:
            raise RecommendationStaleInputError("candidate result version is stale")
        if candidate.validation_status not in {"PASSED", "LIMITED"}:
            raise RecommendationValidationError("candidate is not selectable")
        if not self.repository.episode_version_is_selectable(
            episode_version_id=candidate.episode_version_id,
            owner_user_id=owner_user_id,
        ):
            raise RecommendationValidationError("candidate episode version is no longer selectable")

        run = self.repository.get_run_for_update(run_id=run_id, owner_user_id=owner_user_id)
        if (
            run is None
            or run.question_id != question_id
            or run.snapshot_id != candidate.snapshot_id
        ):
            raise RecommendationNotFoundError("run does not belong to this question")
        if run.status not in {"SUCCEEDED", "LIMITED"} or run.result_status not in {
            "READY",
            "LIMITED",
        }:
            raise RecommendationValidationError("recommendation result is not ready")

        current = self.repository.get_current_selection_for_update(
            question_id=question_id, owner_user_id=owner_user_id
        )
        if current is not None:
            current_items = self.repository.list_selection_items(
                selection_set_id=current.id, owner_user_id=owner_user_id
            )
            if (
                current.run_id == run_id
                and len(current_items) == 1
                and current_items[0].candidate_id == candidate_id
            ):
                return current, False
            if not replace_existing:
                raise MaterialSelectionConflictError("a current selection already exists")

        selection = self.replace_material_selection(
            owner_user_id=owner_user_id,
            project_id=run.project_id,
            question_id=question_id,
            run_id=run_id,
            candidate_ids=(candidate_id,),
        )
        return selection, True

    def clear_current_material_selection(
        self, *, owner_user_id: UUID, question_id: UUID
    ) -> MaterialSelectionSet:
        question = self.repository.get_question_for_update(
            question_id=question_id, owner_user_id=owner_user_id
        )
        if question is None:
            raise RecommendationNotFoundError("question does not exist for this owner")
        current = self.repository.get_current_selection_for_update(
            question_id=question_id, owner_user_id=owner_user_id
        )
        if current is None:
            raise RecommendationNotFoundError("current material selection does not exist")
        current.is_current = False
        current.superseded_at = datetime.now(UTC)
        project = self.repository.get_project_for_update(
            project_id=question.project_id, owner_user_id=owner_user_id
        )
        if project is not None and project.status == "MATERIALS_SELECTED":
            project.status = "READY"
        self.session.flush()
        return current

    def complete_synthetic_run(
        self,
        *,
        owner_user_id: UUID,
        run_id: UUID,
        limited: bool,
    ) -> RecommendationRun:
        """Mark a previously accepted synthetic run complete from a worker seam."""

        run = self.repository.get_run_for_update(run_id=run_id, owner_user_id=owner_user_id)
        if run is None:
            raise RecommendationNotFoundError("recommendation run does not exist for this owner")
        if run.result_origin != "SYNTHETIC":
            raise RecommendationValidationError("only synthetic runs use this executor")
        run.status = "LIMITED" if limited else "SUCCEEDED"
        run.result_status = "LIMITED" if limited else "READY"
        run.limited_analysis = limited
        run.completed_at = datetime.now(UTC)
        self.session.flush()
        return run

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
