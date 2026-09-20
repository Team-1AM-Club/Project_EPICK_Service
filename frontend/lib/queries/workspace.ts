import { useQuery } from "@tanstack/react-query";

import { getApiClient } from "@/lib/api/runtime";
import { personalQueryKeys } from "./query-client";

export type CurrentUser = Readonly<{
  id: string;
  display_name: string;
  email: string | null;
  account_status: string;
  created_at: string;
}>;

export type ResumeItem = Readonly<{
  resource_type: "ACTIVITY_DRAFT" | "PROJECT" | "WAITING_USER_JOB";
  resource_id: string;
  title: string;
  current_step: string | null;
  updated_at: string;
  resume_url: string;
  blocking_reason: string | null;
}>;

export type HomeSummary = Readonly<{
  resume_items: ResumeItem[];
  projects: { in_progress_count: number; needs_review_count: number; recent: unknown[] };
  experience_store: { activity_count: number; draft_count: number };
  notifications: { unread_count: number; critical_count: number };
  running_job_count: number;
}>;

type CursorResponse<T> = Readonly<{ items: T[]; next_cursor: string | null }>;

export function useWorkspaceBootstrap() {
  const api = getApiClient();
  const currentUser = useQuery({
    queryKey: personalQueryKeys.currentUser(),
    queryFn: ({ signal }) => api.request<CurrentUser>("/api/v1/users/me", { signal }),
  });
  const home = useQuery({
    queryKey: personalQueryKeys.home(),
    queryFn: ({ signal }) => api.request<HomeSummary>("/api/v1/home", { signal }),
  });
  const resumeItems = useQuery({
    queryKey: personalQueryKeys.resumeItems(),
    queryFn: ({ signal }) =>
      api.request<CursorResponse<ResumeItem>>("/api/v1/resume-items", { signal }),
  });
  return {
    currentUser,
    home,
    resumeItems,
    isLoading: currentUser.isLoading || home.isLoading || resumeItems.isLoading,
    error: currentUser.error ?? home.error ?? resumeItems.error,
  };
}
