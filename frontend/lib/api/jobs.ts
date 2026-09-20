import type { components } from "@/generated/w1-api";

import { getApiClient } from "./runtime";
import { type ApiRequester, mutationHeaders, withSearch } from "./types";

export type Job = components["schemas"]["JobResponse"];
export type JobListItem = components["schemas"]["JobListItemResponse"];
export type JobList = components["schemas"]["CursorListResponse_JobListItemResponse_"];
export type JobCheckpoint = components["schemas"]["JobCheckpointResponse"];
export type JobRequiredAction = components["schemas"]["JobRequiredActionResponse"];
export type JobActionRequest = components["schemas"]["JobActionRequest"];
export type JobRetryRequest = components["schemas"]["JobRetryRequest"];

export function createJobsApi(client: ApiRequester = getApiClient()) {
  return {
    list: (params: { cursor?: string; limit?: number } = {}, signal?: AbortSignal) =>
      client.request<JobList>(withSearch("/api/v1/jobs", params), { signal }),
    get: (jobId: string, signal?: AbortSignal) =>
      client.request<Job>(`/api/v1/jobs/${jobId}`, { signal }),
    checkpoint: (jobId: string, signal?: AbortSignal) =>
      client.request<JobCheckpoint>(`/api/v1/jobs/${jobId}/checkpoint`, { signal }),
    action: (jobId: string, body: JobActionRequest, idempotencyKey: string) =>
      client.request<Job>(`/api/v1/jobs/${jobId}/actions`, {
        method: "POST",
        headers: mutationHeaders(idempotencyKey),
        body: JSON.stringify(body),
      }),
    retry: (jobId: string, body: JobRetryRequest, idempotencyKey: string) =>
      client.request<Job>(`/api/v1/jobs/${jobId}/retry`, {
        method: "POST",
        headers: mutationHeaders(idempotencyKey),
        body: JSON.stringify(body),
      }),
    cancel: (jobId: string, idempotencyKey: string) =>
      client.request<Job>(`/api/v1/jobs/${jobId}/cancel`, {
        method: "POST",
        headers: mutationHeaders(idempotencyKey),
        body: JSON.stringify({}),
      }),
  };
}

export type JobsApi = ReturnType<typeof createJobsApi>;
