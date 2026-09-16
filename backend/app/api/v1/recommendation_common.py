from __future__ import annotations

from app.api.schemas.recommendations import (
    RecommendationCandidateResponse,
    RecommendationRunResponse,
)
from app.api.schemas.selections import (
    EpisodeReference,
    MaterialSelectionResponse,
    SelectionWarning,
)
from app.models.experience import EpisodeVersion
from app.models.recommendations import (
    MaterialSelectionItem,
    MaterialSelectionSet,
    ProjectSnapshot,
    RecommendationCandidate,
    RecommendationRun,
)


def recommendation_run_response(
    run: RecommendationRun,
    *,
    snapshot: ProjectSnapshot,
    question_version_no: int,
) -> RecommendationRunResponse:
    return RecommendationRunResponse(
        id=run.id,
        project_id=run.project_id,
        question_id=run.question_id,
        question_version=question_version_no,
        snapshot_id=snapshot.id,
        snapshot_version=snapshot.snapshot_no,
        status=run.status,
        result_status=run.result_status,
        result_origin=run.result_origin,
        requested_candidate_limit=run.requested_candidate_limit,
        limited_analysis=run.limited_analysis,
        limitations=list(run.limitations),
        candidates_url=f"/api/v1/recommendation-runs/{run.id}/candidates",
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


def recommendation_candidate_response(
    candidate: RecommendationCandidate,
) -> RecommendationCandidateResponse:
    return RecommendationCandidateResponse(
        id=candidate.id,
        candidate_no=candidate.candidate_no,
        episode_version_id=candidate.episode_version_id,
        match_status=candidate.match_status,
        short_reason=candidate.short_reason,
        strength_summary=candidate.strength_summary,
        limitation_summary=candidate.limitation_summary,
        validation_status=candidate.validation_status,
        result_version=candidate.result_version,
    )


def material_selection_response(
    selection: MaterialSelectionSet,
    *,
    run: RecommendationRun,
    item: MaterialSelectionItem,
    candidate: RecommendationCandidate,
    episode_version: EpisodeVersion,
) -> MaterialSelectionResponse:
    del item
    return MaterialSelectionResponse(
        selection_id=selection.id,
        project_id=run.project_id,
        question_id=selection.question_id,
        recommendation_run_id=selection.run_id,
        candidate_id=candidate.id,
        episode_ref=EpisodeReference(
            episode_id=episode_version.episode_id,
            version=episode_version.version_no,
        ),
        selected_at=selection.selected_at,
        warnings=_candidate_warnings(candidate),
    )


def _candidate_warnings(candidate: RecommendationCandidate) -> list[SelectionWarning]:
    warnings: list[SelectionWarning] = []
    if candidate.match_status == "PARTIAL_RELEVANCE":
        warnings.append(
            SelectionWarning(
                severity="WARNING",
                code="PARTIAL_RELEVANCE",
                message="문항과 일부만 연결되는 후보입니다.",
            )
        )
    if candidate.match_status == "NEEDS_VERIFICATION":
        warnings.append(
            SelectionWarning(
                severity="WARNING",
                code="QUALIFICATION_NEEDS_CONFIRMATION",
                message="일부 지원요건은 현재 등록 정보에서 확인이 필요합니다.",
            )
        )
    if candidate.validation_status == "LIMITED" or candidate.limitation_summary is not None:
        warnings.append(
            SelectionWarning(
                severity="WARNING",
                code="LIMITED_VALIDATION",
                message=candidate.limitation_summary
                or "제한된 검증 결과이므로 내용을 다시 확인해 주세요.",
            )
        )
    return warnings
