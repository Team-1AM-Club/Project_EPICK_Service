"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { createJobsApi, type Job } from "@/lib/api/jobs";
import { getPublicRuntimeConfig } from "@/lib/api/config";
import { jobQueryKeys } from "@/lib/queries/jobs";
import { presentJob } from "./presentation";

type PollingOptions = Readonly<{
  pollMs?: number;
  fetchJob?: (signal?: AbortSignal) => Promise<Job>;
}>;

export function useJobPolling(jobId: string | null, options: PollingOptions = {}) {
  const [browserActive, setBrowserActive] = useState(readBrowserActive);
  const pollMs = options.pollMs ?? getPublicRuntimeConfig().jobPollMs;

  useEffect(() => {
    const update = () => setBrowserActive(readBrowserActive());
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    document.addEventListener("visibilitychange", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);

  return useQuery({
    queryKey: jobQueryKeys.detail(jobId ?? "none"),
    queryFn: ({ signal }) =>
      options.fetchJob ? options.fetchJob(signal) : createJobsApi().get(jobId!, signal),
    enabled: Boolean(jobId) && browserActive,
    retry: false,
    refetchIntervalInBackground: false,
    refetchInterval: (query) => {
      if (!browserActive) return false;
      if (query.state.fetchFailureCount > 0) {
        return Math.min(pollMs * 2 ** query.state.fetchFailureCount, 15_000);
      }
      const job = query.state.data as Job | undefined;
      if (job?.status === "PAUSED_RATE_LIMIT" && (job.failure.retry_after_seconds ?? 0) > 0) {
        return Math.max(pollMs, job.failure.retry_after_seconds! * 1000);
      }
      return job && presentJob(job).shouldPoll ? pollMs : false;
    },
  });
}

function readBrowserActive() {
  if (typeof window === "undefined" || typeof document === "undefined") return true;
  return navigator.onLine !== false && document.visibilityState !== "hidden";
}
