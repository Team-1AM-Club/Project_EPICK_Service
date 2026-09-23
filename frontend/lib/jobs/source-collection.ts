"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createSourceCollectionsApi,
  type SourceCollectionCreate,
  type SourceCollectionsApi,
} from "@/lib/api/source-collections";
import { getPublicRuntimeConfig } from "@/lib/api/config";
import { jobQueryKeys } from "@/lib/queries/jobs";
import { projectQueryKeys } from "@/lib/queries/projects";
import { personalQueryKeys } from "@/lib/queries/query-client";

export const sourceCollectionQueryKeys = {
  all: () => [...personalQueryKeys.all(), "source-collections"] as const,
  progress: (projectId: string, jobId: string) =>
    [...sourceCollectionQueryKeys.all(), projectId, jobId] as const,
  latest: (projectId: string) =>
    [...sourceCollectionQueryKeys.all(), projectId, "latest"] as const,
};

export function sourceCollectionBackoff(baseMs: number, failureCount: number): number {
  return Math.min(baseMs * 2 ** failureCount, 15_000);
}

export function useLatestSourceCollection(
  projectId: string | null,
  api: SourceCollectionsApi = createSourceCollectionsApi(),
) {
  return useQuery({
    queryKey: sourceCollectionQueryKeys.latest(projectId ?? "none"),
    queryFn: ({ signal }) => api.latest(projectId!, signal),
    enabled: Boolean(projectId),
    retry: false,
  });
}

export function useStartSourceCollection(
  projectId: string | null,
  api: SourceCollectionsApi = createSourceCollectionsApi(),
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ body, key }: { body: SourceCollectionCreate; key: string }) => {
      if (!projectId) throw new Error("수집할 지원 프로젝트를 먼저 선택해 주세요.");
      return api.create(projectId, body, key);
    },
    onSuccess: async (accepted) => {
      queryClient.setQueryData(jobQueryKeys.detail(accepted.job_id), {
        id: accepted.job_id,
        job_type: "SOURCE_REGISTRATION",
        status: accepted.status,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: jobQueryKeys.lists() }),
        queryClient.invalidateQueries({ queryKey: projectQueryKeys.detail(projectId!) }),
        queryClient.invalidateQueries({ queryKey: personalQueryKeys.home() }),
        queryClient.invalidateQueries({ queryKey: personalQueryKeys.resumeItems() }),
      ]);
    },
  });
}

export function useSourceCollectionProgress(
  projectId: string | null,
  jobId: string | null,
  api: SourceCollectionsApi = createSourceCollectionsApi(),
) {
  const baseMs = getPublicRuntimeConfig().jobPollMs;
  return useQuery({
    queryKey: sourceCollectionQueryKeys.progress(projectId ?? "none", jobId ?? "none"),
    queryFn: ({ signal }) => api.progress(projectId!, jobId!, signal),
    enabled: Boolean(projectId && jobId),
    retry: false,
    refetchIntervalInBackground: false,
    refetchInterval: (query) => {
      if (query.state.fetchFailureCount > 0) {
        return sourceCollectionBackoff(baseMs, query.state.fetchFailureCount);
      }
      const status = query.state.data?.status;
      return status && ["QUEUED", "RUNNING"].includes(status) ? baseMs : false;
    },
  });
}
