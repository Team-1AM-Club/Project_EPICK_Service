import type { RecommendationRun } from "@/lib/api/recommendations";

export function ResultOriginBadge({ origin }: { origin: RecommendationRun["result_origin"] }) {
  const isSynthetic = origin === "SYNTHETIC";
  return (
    <span
      className={`tag ${isSynthetic ? "amber" : "blue-tag"}`}
      data-result-origin={origin}
      title={isSynthetic ? "W1 합성 실행" : "외부 엔진이 실행하고 W1이 검증·저장한 결과"}
    >
      {isSynthetic ? "합성 검증 결과" : "실제 엔진 결과"}
    </span>
  );
}
