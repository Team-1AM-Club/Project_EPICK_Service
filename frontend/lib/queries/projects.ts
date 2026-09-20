import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createProjectsApi,
  type ProjectCreate,
  type ProjectJobPostingLink,
  type ProjectListParams,
  type ProjectUpdate,
} from "@/lib/api/projects";
import { personalQueryKeys } from "./query-client";

export const projectQueryKeys = {
  all: () => [...personalQueryKeys.all(), "projects"] as const,
  lists: () => [...projectQueryKeys.all(), "list"] as const,
  list: (params: ProjectListParams = {}) => [...projectQueryKeys.lists(), params] as const,
  detail: (id: string) => [...projectQueryKeys.all(), "detail", id] as const,
  companies: (query = "") => [...personalQueryKeys.all(), "companies", query] as const,
};

export function useProjects(params: ProjectListParams = {}) {
  return useQuery({
    queryKey: projectQueryKeys.list(params),
    queryFn: ({ signal }) => createProjectsApi().list(params, signal),
  });
}

export function useProject(projectId: string | null) {
  return useQuery({
    queryKey: projectQueryKeys.detail(projectId ?? "none"),
    queryFn: ({ signal }) => createProjectsApi().get(projectId!, signal),
    enabled: Boolean(projectId),
  });
}

export function useCompanies(query = "") {
  return useQuery({
    queryKey: projectQueryKeys.companies(query),
    queryFn: ({ signal }) => createProjectsApi().listCompanies(query, signal),
  });
}

export function useProjectMutations() {
  const queryClient = useQueryClient();
  const api = createProjectsApi();
  const invalidate = async (id?: string) => {
    await queryClient.invalidateQueries({ queryKey: projectQueryKeys.lists() });
    if (id) await queryClient.invalidateQueries({ queryKey: projectQueryKeys.detail(id) });
    await queryClient.invalidateQueries({ queryKey: personalQueryKeys.home() });
    await queryClient.invalidateQueries({ queryKey: personalQueryKeys.resumeItems() });
  };
  return {
    create: useMutation({
      mutationFn: ({ body, key }: { body: ProjectCreate; key: string }) => api.create(body, key),
      onSuccess: (value) => invalidate(value.id),
    }),
    update: useMutation({
      mutationFn: ({ id, version, body, key }: { id: string; version: number; body: ProjectUpdate; key: string }) =>
        api.update(id, version, body, key),
      onSuccess: (value) => invalidate(value.id),
    }),
    linkJobPosting: useMutation({
      mutationFn: ({ id, version, body, key }: { id: string; version: number; body: ProjectJobPostingLink; key: string }) =>
        api.linkJobPosting(id, version, body, key),
      onSuccess: (value) => invalidate(value.id),
    }),
  };
}
