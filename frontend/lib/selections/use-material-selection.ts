"use client";

import { useMemo, useRef } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import type {
  RecommendationCandidate,
  RecommendationRun,
} from "@/lib/api/recommendations";
import {
  createSelectionsApi,
  type MaterialSelection,
  type SelectionsApi,
} from "@/lib/api/selections";
import { createIdempotentIntent, type IdempotentIntent } from "@/lib/api/idempotency";
import { projectQueryKeys } from "@/lib/queries/projects";
import { questionQueryKeys } from "@/lib/queries/questions";
import { selectionQueryKeys } from "@/lib/queries/selections";
import { clientGeneration } from "@/lib/queries/query-client";

type SelectionApi = Pick<SelectionsApi, "select" | "clear">;
type IntentFactory = (payload: unknown) => IdempotentIntent;

export function createMaterialSelectionExecutor(
  api: SelectionApi = createSelectionsApi(),
  makeIntent: IntentFactory = createIdempotentIntent,
) {
  const inFlight = new Map<string, Promise<MaterialSelection | void>>();
  function once<T extends MaterialSelection | void>(
    signature: string,
    payload: unknown,
    run: (key: string) => Promise<T>,
  ): Promise<T> {
    const current = inFlight.get(signature);
    if (current) return current as Promise<T>;
    const pending = run(makeIntent(payload).key).finally(() => inFlight.delete(signature));
    inFlight.set(signature, pending);
    return pending;
  }
  return {
    select(run: RecommendationRun, candidate: RecommendationCandidate, replaceExisting: boolean) {
      const body = {
        question_id: run.question_id,
        recommendation_run_id: run.id,
        result_version: candidate.result_version,
        replace_existing: replaceExisting,
      };
      return once(
        `select:${run.question_id}:${candidate.id}:${candidate.result_version}:${replaceExisting}`,
        body,
        (key) => api.select(candidate.id, body, key),
      );
    },
    clear(questionId: string) {
      return once(`clear:${questionId}`, { questionId }, (key) => api.clear(questionId, key));
    },
  };
}
type ResponseToken = Readonly<{ questionId: string; revision: number; generation: number }>;

export function createSelectionResponseGuard(getGeneration = () => clientGeneration.current()) {
  const revisions = new Map<string, number>();
  return {
    begin(questionId: string): ResponseToken {
      const revision = (revisions.get(questionId) ?? 0) + 1;
      revisions.set(questionId, revision);
      return { questionId, revision, generation: getGeneration() };
    },
    isCurrent(token: ResponseToken) {
      return (
        token.generation === getGeneration() &&
        revisions.get(token.questionId) === token.revision
      );
    },
  };
}

export function selectionInvalidationTargets(questionId: string, projectId?: string | null) {
  return [
    selectionQueryKeys.current(questionId),
    questionQueryKeys.detail(questionId),
    projectId ? projectQueryKeys.detail(projectId) : projectQueryKeys.all(),
    projectQueryKeys.lists(),
  ];
}

export function useMaterialSelectionActions(
  run: RecommendationRun | null,
  currentSelection: MaterialSelection | null,
) {
  const queryClient = useQueryClient();
  const executor = useMemo(() => createMaterialSelectionExecutor(), []);
  const guard = useRef(createSelectionResponseGuard()).current;
  const mutation = useMutation({
    mutationFn: async (
      input:
        | { kind: "select"; candidate: RecommendationCandidate }
        | { kind: "clear" },
    ) => {
      if (!run) throw new Error("추천 결과를 먼저 확인해 주세요.");
      const token = guard.begin(run.question_id);
      if (input.kind === "clear") {
        await executor.clear(run.question_id);
        return { input, result: null, token };
      }
      const replace = Boolean(
        currentSelection && currentSelection.candidate_id !== input.candidate.id,
      );
      const result = await executor.select(run, input.candidate, replace);
      return { input, result, token };
    },
    onSuccess: async ({ result, token }) => {
      if (!run || !guard.isCurrent(token)) return;
      queryClient.setQueryData(selectionQueryKeys.current(run.question_id), result);
      const targets = selectionInvalidationTargets(run.question_id, run.project_id);
      await Promise.all(
        targets.map((queryKey) => queryClient.invalidateQueries({ queryKey, exact: true })),
      );
    },
  });
  return {
    select: (candidate: RecommendationCandidate) =>
      mutation.mutateAsync({ kind: "select", candidate }),
    clear: () => mutation.mutateAsync({ kind: "clear" }),
    pending: mutation.isPending,
    error: mutation.error,
  };
}
