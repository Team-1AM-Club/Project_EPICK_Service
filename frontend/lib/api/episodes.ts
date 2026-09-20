import type { components } from "@/generated/w1-api";

import { getApiClient } from "./runtime";
import { type ApiRequester, mutationHeaders } from "./types";

export type EpisodeCreate = components["schemas"]["EpisodeCreateRequest"];
export type EpisodeUpdate = components["schemas"]["EpisodeUpdateRequest"];
export type Episode = components["schemas"]["EpisodeResponse"];
export type EpisodeMutation = components["schemas"]["EpisodeMutationResponse"];
export type EpisodeListItem = components["schemas"]["EpisodeListItemResponse"];

export function createEpisodesApi(client: ApiRequester = getApiClient()) {
  return {
    list: (activityId: string, signal?: AbortSignal) =>
      client.request<EpisodeListItem[]>(`/api/v1/activities/${activityId}/episodes`, { signal }),
    get: (episodeId: string, signal?: AbortSignal) =>
      client.request<Episode>(`/api/v1/episodes/${episodeId}`, { signal }),
    create: (activityId: string, body: EpisodeCreate, idempotencyKey: string) =>
      client.request<Episode>(`/api/v1/activities/${activityId}/episodes`, {
        method: "POST",
        headers: mutationHeaders(idempotencyKey),
        body: JSON.stringify(body),
      }),
    update: (episodeId: string, version: number, body: EpisodeUpdate, idempotencyKey: string) =>
      client.request<EpisodeMutation>(`/api/v1/episodes/${episodeId}`, {
        method: "PATCH",
        headers: mutationHeaders(idempotencyKey, version),
        body: JSON.stringify(body),
      }),
    complete: (episodeId: string, version: number, idempotencyKey: string) =>
      client.request<EpisodeMutation>(`/api/v1/episodes/${episodeId}/complete`, {
        method: "POST",
        headers: mutationHeaders(idempotencyKey, version),
        body: JSON.stringify({}),
      }),
  };
}

export type EpisodesApi = ReturnType<typeof createEpisodesApi>;
