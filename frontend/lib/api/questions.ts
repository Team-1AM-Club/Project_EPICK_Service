import type { components } from "@/generated/w1-api";

import { getApiClient } from "./runtime";
import { type ApiRequester, mutationHeaders } from "./types";

export type QuestionCreate = components["schemas"]["QuestionCreateRequest"];
export type QuestionUpdate = components["schemas"]["QuestionUpdateRequest"];
export type Question = components["schemas"]["QuestionResponse"];
export type QuestionMutation = components["schemas"]["QuestionMutationResponse"];
export type QuestionListItem = components["schemas"]["QuestionListItemResponse"];

export function createQuestionsApi(client: ApiRequester = getApiClient()) {
  return {
    list: (projectId: string, signal?: AbortSignal) =>
      client.request<QuestionListItem[]>(`/api/v1/application-projects/${projectId}/questions`, {
        signal,
      }),
    get: (questionId: string, signal?: AbortSignal) =>
      client.request<Question>(`/api/v1/questions/${questionId}`, { signal }),
    create: (projectId: string, body: QuestionCreate, idempotencyKey: string) =>
      client.request<Question>(`/api/v1/application-projects/${projectId}/questions`, {
        method: "POST",
        headers: mutationHeaders(idempotencyKey),
        body: JSON.stringify(body),
      }),
    update: (questionId: string, version: number, body: QuestionUpdate, idempotencyKey: string) =>
      client.request<QuestionMutation>(`/api/v1/questions/${questionId}`, {
        method: "PATCH",
        headers: mutationHeaders(idempotencyKey, version),
        body: JSON.stringify(body),
      }),
    archive: (questionId: string, version: number, idempotencyKey: string) =>
      client.request<void>(`/api/v1/questions/${questionId}`, {
        method: "DELETE",
        headers: mutationHeaders(idempotencyKey, version),
      }),
  };
}

export type QuestionsApi = ReturnType<typeof createQuestionsApi>;
