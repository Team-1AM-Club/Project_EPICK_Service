import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createQuestionsApi, type QuestionCreate, type QuestionUpdate } from "@/lib/api/questions";
import { personalQueryKeys } from "./query-client";

export const questionQueryKeys = {
  all: () => [...personalQueryKeys.all(), "questions"] as const,
  list: (projectId: string) => [...questionQueryKeys.all(), "project", projectId] as const,
  detail: (questionId: string) => [...questionQueryKeys.all(), "detail", questionId] as const,
};

export function useQuestions(projectId: string | null) {
  return useQuery({
    queryKey: questionQueryKeys.list(projectId ?? "none"),
    queryFn: ({ signal }) => createQuestionsApi().list(projectId!, signal),
    enabled: Boolean(projectId),
  });
}

export function useQuestionMutations() {
  const queryClient = useQueryClient();
  const api = createQuestionsApi();
  const invalidate = async (projectId: string, questionId?: string) => {
    await queryClient.invalidateQueries({ queryKey: questionQueryKeys.list(projectId) });
    if (questionId) await queryClient.invalidateQueries({ queryKey: questionQueryKeys.detail(questionId) });
  };
  return {
    create: useMutation({
      mutationFn: ({ projectId, body, key }: { projectId: string; body: QuestionCreate; key: string }) =>
        api.create(projectId, body, key),
      onSuccess: (value) => invalidate(value.project_id, value.id),
    }),
    update: useMutation({
      mutationFn: ({ id, version, body, key }: { id: string; version: number; body: QuestionUpdate; key: string }) =>
        api.update(id, version, body, key),
      onSuccess: (value) => invalidate(value.project_id, value.id),
    }),
    archive: useMutation({
      mutationFn: ({ id, projectId, version, key }: { id: string; projectId: string; version: number; key: string }) =>
        api.archive(id, version, key).then(() => ({ id, projectId })),
      onSuccess: (value) => invalidate(value.projectId, value.id),
    }),
  };
}
