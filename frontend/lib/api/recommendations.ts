import type { components } from "@/generated/w1-api";

import { ApiError } from "./client";
import { getApiClient } from "./runtime";
import { type ApiRequester, mutationHeaders, withSearch } from "./types";

export type RecommendationRunCreate = components["schemas"]["RecommendationRunCreateRequest"];
export type RecommendationRun = components["schemas"]["RecommendationRunResponse"];
export type RecommendationCandidate = components["schemas"]["RecommendationCandidateResponse"];
export type RecommendationCandidateList =
  components["schemas"]["CursorListResponse_RecommendationCandidateResponse_"];

export function createRecommendationsApi(client: ApiRequester = getApiClient()) {
  return {
    createRun: (
      questionId: string,
      body: RecommendationRunCreate,
      idempotencyKey: string,
    ) =>
      client.request<RecommendationRun>(
        `/api/v1/questions/${questionId}/recommendation-runs`,
        {
          method: "POST",
          headers: mutationHeaders(idempotencyKey),
          body: JSON.stringify(body),
        },
      ),
    getRun: (runId: string, signal?: AbortSignal) =>
      client.request<RecommendationRun>(`/api/v1/recommendation-runs/${runId}`, { signal }),
    listCandidates: (
      runId: string,
      params: { cursor?: string; limit?: number } = {},
      signal?: AbortSignal,
    ) =>
      client.request<RecommendationCandidateList>(
        withSearch(`/api/v1/recommendation-runs/${runId}/candidates`, params),
        { signal },
      ),
    getCandidate: (candidateId: string, signal?: AbortSignal) =>
      client.request<RecommendationCandidate>(
        `/api/v1/recommendation-candidates/${candidateId}`,
        { signal },
      ),
  };
}

export function describeRecommendationError(error: unknown): string {
  if (!(error instanceof ApiError)) return "추천 결과를 불러오지 못했습니다.";
  switch (error.code) {
    case "STALE_INPUT":
      return "입력이 변경되어 이 추천 결과를 사용할 수 없습니다.";
    case "ACTION_NOT_ALLOWED":
      return "현재 선택을 먼저 확인하거나 교체를 선택해 주세요.";
    case "RESOURCE_NOT_FOUND":
      return "추천 결과를 찾을 수 없거나 접근할 수 없습니다.";
    default:
      return error.retryable
        ? "추천 결과를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."
        : "추천 결과를 불러오지 못했습니다.";
  }
}

export type RecommendationsApi = ReturnType<typeof createRecommendationsApi>;
