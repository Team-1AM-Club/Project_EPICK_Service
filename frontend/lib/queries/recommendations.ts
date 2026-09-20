import { useMutation, useQuery } from "@tanstack/react-query";

import {
  createRecommendationsApi,
  type RecommendationRunCreate,
} from "@/lib/api/recommendations";
import { personalQueryKeys } from "./query-client";

export const recommendationQueryKeys = {
  all: () => [...personalQueryKeys.all(), "recommendations"] as const,
  runs: () => [...recommendationQueryKeys.all(), "runs"] as const,
  run: (runId: string) => [...recommendationQueryKeys.runs(), runId] as const,
  candidateLists: (runId: string) =>
    [...recommendationQueryKeys.run(runId), "candidates"] as const,
  candidates: (runId: string, params: { cursor?: string; limit?: number } = {}) =>
    [...recommendationQueryKeys.candidateLists(runId), params] as const,
  candidate: (candidateId: string) =>
    [...recommendationQueryKeys.all(), "candidate", candidateId] as const,
};

export function useRecommendationRun(runId: string | null) {
  return useQuery({
    queryKey: recommendationQueryKeys.run(runId ?? "none"),
    queryFn: ({ signal }) => createRecommendationsApi().getRun(runId!, signal),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "PENDING" || status === "RUNNING" ? 3_000 : false;
    },
  });
}
export function useRecommendationCandidates(runId: string | null) {
  return useQuery({
    queryKey: recommendationQueryKeys.candidates(runId ?? "none", { limit: 20 }),
    queryFn: ({ signal }) =>
      createRecommendationsApi().listCandidates(runId!, { limit: 20 }, signal),
    enabled: Boolean(runId),
  });
}

export function useCreateRecommendationRun() {
  return useMutation({
    mutationFn: ({
      questionId,
      body,
      key,
    }: {
      questionId: string;
      body: RecommendationRunCreate;
      key: string;
    }) => createRecommendationsApi().createRun(questionId, body, key),
  });
}
