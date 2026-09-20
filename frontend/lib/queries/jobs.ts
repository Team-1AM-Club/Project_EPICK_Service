import { useQuery } from "@tanstack/react-query";

import { createJobsApi } from "@/lib/api/jobs";
import { personalQueryKeys } from "./query-client";

export const jobQueryKeys = {
  all: () => [...personalQueryKeys.all(), "jobs"] as const,
  lists: () => [...jobQueryKeys.all(), "list"] as const,
  list: (params: { cursor?: string; limit?: number } = {}) => [...jobQueryKeys.lists(), params] as const,
  detail: (id: string) => [...jobQueryKeys.all(), "detail", id] as const,
  checkpoint: (id: string) => [...jobQueryKeys.detail(id), "checkpoint"] as const,
};

export function useJobs(params: { cursor?: string; limit?: number } = {}) {
  return useQuery({
    queryKey: jobQueryKeys.list(params),
    queryFn: ({ signal }) => createJobsApi().list(params, signal),
  });
}

export function useJobCheckpoint(jobId: string | null) {
  return useQuery({
    queryKey: jobQueryKeys.checkpoint(jobId ?? "none"),
    queryFn: ({ signal }) => createJobsApi().checkpoint(jobId!, signal),
    enabled: Boolean(jobId),
  });
}
