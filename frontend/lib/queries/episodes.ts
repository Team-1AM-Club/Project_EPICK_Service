import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createEpisodesApi, type EpisodeCreate, type EpisodeUpdate } from "@/lib/api/episodes";
import { activityQueryKeys } from "./activities";
import { personalQueryKeys } from "./query-client";

export const episodeQueryKeys = {
  all: () => [...personalQueryKeys.all(), "episodes"] as const,
  list: (activityId: string) => [...episodeQueryKeys.all(), "activity", activityId] as const,
  detail: (episodeId: string) => [...episodeQueryKeys.all(), "detail", episodeId] as const,
};

export function useEpisodes(activityId: string | null) {
  return useQuery({
    queryKey: episodeQueryKeys.list(activityId ?? "none"),
    queryFn: ({ signal }) => createEpisodesApi().list(activityId!, signal),
    enabled: Boolean(activityId),
  });
}

export function useEpisode(episodeId: string | null) {
  return useQuery({
    queryKey: episodeQueryKeys.detail(episodeId ?? "none"),
    queryFn: ({ signal }) => createEpisodesApi().get(episodeId!, signal),
    enabled: Boolean(episodeId),
  });
}

export function useEpisodeMutations() {
  const queryClient = useQueryClient();
  const api = createEpisodesApi();
  const invalidate = async (activityId: string, episodeId?: string) => {
    await queryClient.invalidateQueries({ queryKey: episodeQueryKeys.list(activityId) });
    if (episodeId) await queryClient.invalidateQueries({ queryKey: episodeQueryKeys.detail(episodeId) });
    await queryClient.invalidateQueries({ queryKey: activityQueryKeys.detail(activityId) });
  };
  return {
    create: useMutation({
      mutationFn: ({ activityId, body, key }: { activityId: string; body: EpisodeCreate; key: string }) =>
        api.create(activityId, body, key),
      onSuccess: (value) => invalidate(value.activity_id, value.id),
    }),
    update: useMutation({
      mutationFn: ({ id, version, body, key }: { id: string; version: number; body: EpisodeUpdate; key: string }) =>
        api.update(id, version, body, key),
      onSuccess: (value) => invalidate(value.activity_id, value.id),
    }),
    complete: useMutation({
      mutationFn: ({ id, version, key }: { id: string; version: number; key: string }) =>
        api.complete(id, version, key),
      onSuccess: (value) => invalidate(value.activity_id, value.id),
    }),
  };
}
