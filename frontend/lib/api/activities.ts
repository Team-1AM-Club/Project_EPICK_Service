import type { components } from "@/generated/w1-api";

import { getApiClient } from "./runtime";
import { type ApiRequester, mutationHeaders, withSearch } from "./types";

export type ActivityCreate = components["schemas"]["ActivityCreateRequest"];
export type ActivityUpdate = components["schemas"]["ActivityUpdateRequest"];
export type Activity = components["schemas"]["ActivityResponse"];
export type ActivityMutation = components["schemas"]["ActivityMutationResponse"];
export type ActivityListItem = components["schemas"]["ActivityListItemResponse"];
export type ActivityList = components["schemas"]["CursorListResponse_ActivityListItemResponse_"];

export type ActivityListParams = Readonly<{
  status?: "DRAFT" | "COMPLETED";
  activityType?: string;
  usageEnabled?: boolean;
  q?: string;
  sort?: string;
  cursor?: string;
  limit?: number;
}>;

export function createActivitiesApi(client: ApiRequester = getApiClient()) {
  return {
    list: (params: ActivityListParams = {}, signal?: AbortSignal) =>
      client.request<ActivityList>(
        withSearch("/api/v1/activities", {
          q: params.q,
          status: params.status,
          activity_type: params.activityType,
          usage_enabled: params.usageEnabled,
          sort: params.sort,
          cursor: params.cursor,
          limit: params.limit,
        }),
        { signal },
      ),
    get: (activityId: string, signal?: AbortSignal) =>
      client.request<Activity>(`/api/v1/activities/${activityId}`, { signal }),
    create: (body: ActivityCreate, idempotencyKey: string) =>
      client.request<Activity>("/api/v1/activities", {
        method: "POST",
        headers: mutationHeaders(idempotencyKey),
        body: JSON.stringify(body),
      }),
    update: (activityId: string, version: number, body: ActivityUpdate, idempotencyKey: string) =>
      client.request<ActivityMutation>(`/api/v1/activities/${activityId}`, {
        method: "PATCH",
        headers: mutationHeaders(idempotencyKey, version),
        body: JSON.stringify(body),
      }),
    complete: (activityId: string, version: number, idempotencyKey: string) =>
      client.request<ActivityMutation>(`/api/v1/activities/${activityId}/complete`, {
        method: "POST",
        headers: mutationHeaders(idempotencyKey, version),
        body: JSON.stringify({}),
      }),
  };
}

export type ActivitiesApi = ReturnType<typeof createActivitiesApi>;
