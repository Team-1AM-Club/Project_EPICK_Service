import type { components } from "@/generated/w1-api";

import { ApiError } from "./client";
import { getApiClient } from "./runtime";
import { type ApiRequester, mutationHeaders } from "./types";

export type CandidateSelection = components["schemas"]["CandidateSelectionRequest"];
export type MaterialSelection = components["schemas"]["MaterialSelectionResponse"];

export function createSelectionsApi(client: ApiRequester = getApiClient()) {
  return {
    getCurrent: (questionId: string, signal?: AbortSignal) =>
      client.request<MaterialSelection>(`/api/v1/questions/${questionId}/selection`, {
        signal,
      }),
    getCurrentOrNull: async (questionId: string, signal?: AbortSignal) => {
      try {
        return await client.request<MaterialSelection>(
          `/api/v1/questions/${questionId}/selection`,
          { signal },
        );
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    select: (candidateId: string, body: CandidateSelection, idempotencyKey: string) =>
      client.request<MaterialSelection>(
        `/api/v1/recommendation-candidates/${candidateId}/select`,
        {
          method: "POST",
          headers: mutationHeaders(idempotencyKey),
          body: JSON.stringify(body),
        },
      ),
    clear: (questionId: string, idempotencyKey: string) =>
      client.request<void>(`/api/v1/questions/${questionId}/selection`, {
        method: "DELETE",
        headers: mutationHeaders(idempotencyKey),
      }),
  };
}

export type SelectionsApi = ReturnType<typeof createSelectionsApi>;
