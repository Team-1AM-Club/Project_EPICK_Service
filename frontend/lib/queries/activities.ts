import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createActivitiesApi,
  type ActivityCreate,
  type ActivityListParams,
  type ActivityUpdate,
} from "@/lib/api/activities";
import { personalQueryKeys } from "./query-client";

export const activityQueryKeys = {
  all: () => [...personalQueryKeys.all(), "activities"] as const,
  lists: () => [...activityQueryKeys.all(), "list"] as const,
  list: (params: ActivityListParams = {}) => [...activityQueryKeys.lists(), params] as const,
  detail: (id: string) => [...activityQueryKeys.all(), "detail", id] as const,
};

export function useActivities(params: ActivityListParams = {}) {
  return useQuery({
    queryKey: activityQueryKeys.list(params),
    queryFn: ({ signal }) => createActivitiesApi().list(params, signal),
  });
}

export function useActivity(activityId: string | null) {
  return useQuery({
    queryKey: activityQueryKeys.detail(activityId ?? "none"),
    queryFn: ({ signal }) => createActivitiesApi().get(activityId!, signal),
    enabled: Boolean(activityId),
  });
}

export function useActivityMutations() {
  const queryClient = useQueryClient();
  const api = createActivitiesApi();
  const invalidate = async (id?: string) => {
    await queryClient.invalidateQueries({ queryKey: activityQueryKeys.lists() });
    if (id) await queryClient.invalidateQueries({ queryKey: activityQueryKeys.detail(id) });
    await queryClient.invalidateQueries({ queryKey: personalQueryKeys.home() });
    await queryClient.invalidateQueries({ queryKey: personalQueryKeys.resumeItems() });
  };
  return {
    create: useMutation({
      mutationFn: ({ body, key }: { body: ActivityCreate; key: string }) => api.create(body, key),
      onSuccess: (value) => invalidate(value.id),
    }),
    update: useMutation({
      mutationFn: ({ id, version, body, key }: { id: string; version: number; body: ActivityUpdate; key: string }) =>
        api.update(id, version, body, key),
      onSuccess: (value) => invalidate(value.id),
    }),
    complete: useMutation({
      mutationFn: ({ id, version, key }: { id: string; version: number; key: string }) =>
        api.complete(id, version, key),
      onSuccess: (value) => invalidate(value.id),
    }),
  };
}
