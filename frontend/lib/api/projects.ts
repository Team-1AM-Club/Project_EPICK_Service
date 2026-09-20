import type { components } from "@/generated/w1-api";

import { getApiClient } from "./runtime";
import { type ApiRequester, mutationHeaders, withSearch } from "./types";

export type ProjectCreate = components["schemas"]["ProjectCreateRequest"];
export type ProjectUpdate = components["schemas"]["ProjectUpdateRequest"];
export type Project = components["schemas"]["ProjectResponse"];
export type ProjectMutation = components["schemas"]["ProjectMutationResponse"];
export type ProjectListItem = components["schemas"]["ProjectListItemResponse"];
export type ProjectList = components["schemas"]["CursorListResponse_ProjectListItemResponse_"];
export type Company = components["schemas"]["CompanyResponse"];
export type CompanyList = components["schemas"]["CursorListResponse_CompanyResponse_"];
export type ProjectJobPostingLink = components["schemas"]["ProjectJobPostingLinkRequest"];

export type ProjectListParams = Readonly<{
  cursor?: string;
  limit?: number;
}>;

export function createProjectsApi(client: ApiRequester = getApiClient()) {
  return {
    list: (params: ProjectListParams = {}, signal?: AbortSignal) =>
      client.request<ProjectList>(
        withSearch("/api/v1/application-projects", {
          cursor: params.cursor,
          limit: params.limit,
        }),
        { signal },
      ),
    get: (projectId: string, signal?: AbortSignal) =>
      client.request<Project>(`/api/v1/application-projects/${projectId}`, { signal }),
    create: (body: ProjectCreate, idempotencyKey: string) =>
      client.request<Project>("/api/v1/application-projects", {
        method: "POST",
        headers: mutationHeaders(idempotencyKey),
        body: JSON.stringify(body),
      }),
    update: (projectId: string, version: number, body: ProjectUpdate, idempotencyKey: string) =>
      client.request<ProjectMutation>(`/api/v1/application-projects/${projectId}`, {
        method: "PATCH",
        headers: mutationHeaders(idempotencyKey, version),
        body: JSON.stringify(body),
      }),
    linkJobPosting: (
      projectId: string,
      version: number,
      body: ProjectJobPostingLink,
      idempotencyKey: string,
    ) =>
      client.request<ProjectMutation>(`/api/v1/application-projects/${projectId}/job-posting`, {
        method: "POST",
        headers: mutationHeaders(idempotencyKey, version),
        body: JSON.stringify(body),
      }),
    listCompanies: (query = "", signal?: AbortSignal) =>
      client.request<CompanyList>(withSearch("/api/v1/companies", { query, limit: 100 }), { signal }),
  };
}

export type ProjectsApi = ReturnType<typeof createProjectsApi>;
