import type {
  RecommendationCandidate,
  RecommendationRun,
} from "@/lib/api/recommendations";

export type CandidateAvailability = Readonly<{
  selectable: boolean;
  stale: boolean;
  limited: boolean;
  reason: string | null;
}>;

export function candidateAvailability(
  run: RecommendationRun,
  candidate: RecommendationCandidate,
  currentQuestionVersion: number,
): CandidateAvailability {
  if (run.question_version !== currentQuestionVersion) {
    return {
      selectable: false,
      stale: true,
      limited: false,
      reason: "문항이 변경되어 이전 추천을 선택할 수 없습니다.",
    };
  }
  if (
    !["SUCCEEDED", "LIMITED"].includes(run.status) ||
    !["READY", "LIMITED"].includes(run.result_status)
  ) {
    return {
      selectable: false,
      stale: false,
      limited: false,
      reason: "아직 선택할 수 있는 추천 결과가 아닙니다.",
    };
  }
  if (!["PASSED", "LIMITED"].includes(candidate.validation_status)) {
    return {
      selectable: false,
      stale: false,
      limited: false,
      reason: "검증이 완료되지 않아 이 후보를 선택할 수 없습니다.",
    };
  }
  const limited =
    run.status === "LIMITED" ||
    run.result_status === "LIMITED" ||
    run.limited_analysis ||
    candidate.validation_status === "LIMITED" ||
    candidate.limitation_summary !== null;
  return { selectable: true, stale: false, limited, reason: null };
}
export function recommendationRunState(
  run: RecommendationRun,
  currentQuestionVersion: number,
): "pending" | "ready" | "limited" | "stale" | "failed" {
  if (run.question_version !== currentQuestionVersion) return "stale";
  if (run.status === "FAILED" || run.result_status === "FAILED" || run.status === "CANCELLED") {
    return "failed";
  }
  if (run.status === "PENDING" || run.status === "RUNNING" || run.result_status === "PENDING") {
    return "pending";
  }
  return run.status === "LIMITED" || run.result_status === "LIMITED" || run.limited_analysis
    ? "limited"
    : "ready";
}
