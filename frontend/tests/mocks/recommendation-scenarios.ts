import type { RecommendationCandidate, RecommendationRun } from "@/lib/api/recommendations";
import type { MaterialSelection } from "@/lib/api/selections";

export function makeRecommendationRun(
  overrides: Partial<RecommendationRun> = {},
): RecommendationRun {
  return {
    id: "run-1",
    project_id: "project-1",
    question_id: "question-1",
    question_version: 2,
    snapshot_id: "snapshot-1",
    snapshot_version: 1,
    status: "SUCCEEDED",
    result_status: "READY",
    result_origin: "ENGINE",
    requested_candidate_limit: 5,
    limited_analysis: false,
    limitations: [],
    candidates_url: "/api/v1/recommendation-runs/run-1/candidates",
    created_at: "2026-09-20T00:00:00Z",
    completed_at: "2026-09-20T00:01:00Z",
    ...overrides,
  };
}
export function makeRecommendationCandidate(
  overrides: Partial<RecommendationCandidate> = {},
): RecommendationCandidate {
  return {
    id: "candidate-1",
    candidate_no: 1,
    episode_version_id: "episode-version-1",
    match_status: "DIRECT_MATCH",
    short_reason: "문항의 협업 요구와 직접 연결됩니다.",
    strength_summary: "역할과 행동이 구체적입니다.",
    limitation_summary: null,
    validation_status: "PASSED",
    result_version: "result-v2",
    ...overrides,
  };
}

export function makeMaterialSelection(
  overrides: Partial<MaterialSelection> = {},
): MaterialSelection {
  return {
    selection_id: "selection-1",
    project_id: "project-1",
    question_id: "question-1",
    recommendation_run_id: "run-1",
    candidate_id: "candidate-1",
    episode_ref: { episode_id: "episode-1", version: 1 },
    selected_at: "2026-09-20T00:02:00Z",
    warnings: [],
    ...overrides,
  };
}
