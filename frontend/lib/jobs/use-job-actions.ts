"use client";

import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  createJobsApi,
  type Job,
  type JobActionRequest,
  type JobCheckpoint,
  type JobRequiredAction,
  type JobRetryRequest,
  type JobsApi,
} from "@/lib/api/jobs";
import { createIdempotentIntent, type IdempotentIntent } from "@/lib/api/idempotency";
import { jobQueryKeys } from "@/lib/queries/jobs";
import { personalQueryKeys } from "@/lib/queries/query-client";

type ActionApi = Pick<JobsApi, "action" | "retry" | "cancel">;
type IntentFactory = (payload: unknown) => IdempotentIntent;

export function createJobActionExecutor(
  api: ActionApi = createJobsApi(),
  makeIntent: IntentFactory = createIdempotentIntent,
) {
  const inFlight = new Map<string, Promise<Job>>();

  function once(signature: string, payload: unknown, run: (key: string) => Promise<Job>) {
    const current = inFlight.get(signature);
    if (current) return current;
    const key = makeIntent(payload).key;
    const pending = run(key).finally(() => inFlight.delete(signature));
    inFlight.set(signature, pending);
    return pending;
  }

  return {
    submit(jobId: string, action: JobRequiredAction) {
      const body: JobActionRequest = {
        required_action_id: action.id,
        action: action.code,
        expected_input_version: action.expected_input_version,
        expected_result_version: action.expected_result_version,
        acknowledge_rate_limit: action.code === "RETRY",
      };
      return once(`action:${jobId}:${action.id}:${action.code}`, body, (key) =>
        api.action(jobId, body, key),
      );
    },
    retry(jobId: string, action: JobRequiredAction, checkpoint: JobCheckpoint) {
      if (!checkpoint.available || !checkpoint.last_completed_stage) {
        throw new Error("현재 재시작 가능한 체크포인트가 없습니다.");
      }
      const body: JobRetryRequest = {
        required_action_id: action.id,
        from_stage: checkpoint.last_completed_stage,
        expected_input_version: action.expected_input_version,
        expected_result_version: action.expected_result_version,
        acknowledge_rate_limit: true,
      };
      return once(`retry:${jobId}:${action.id}:${checkpoint.last_completed_stage}`, body, (key) =>
        api.retry(jobId, body, key),
      );
    },
    cancel(jobId: string) {
      return once(`cancel:${jobId}`, {}, (key) => api.cancel(jobId, key));
    },
  };
}

export function useJobActions(jobId: string) {
  const queryClient = useQueryClient();
  const executor = useMemo(() => createJobActionExecutor(), []);
  const mutation = useMutation({
    mutationFn: (
      input:
        | { kind: "action"; action: JobRequiredAction }
        | { kind: "retry"; action: JobRequiredAction; checkpoint: JobCheckpoint }
        | { kind: "cancel" },
    ) => {
      if (input.kind === "action") return executor.submit(jobId, input.action);
      if (input.kind === "retry") return executor.retry(jobId, input.action, input.checkpoint);
      return executor.cancel(jobId);
    },
    onSuccess: async (job) => {
      queryClient.setQueryData(jobQueryKeys.detail(jobId), job);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: jobQueryKeys.lists() }),
        queryClient.invalidateQueries({ queryKey: personalQueryKeys.home() }),
        queryClient.invalidateQueries({ queryKey: personalQueryKeys.resumeItems() }),
      ]);
    },
  });
  return {
    submit: (action: JobRequiredAction) => mutation.mutateAsync({ kind: "action", action }),
    retry: (action: JobRequiredAction, checkpoint: JobCheckpoint) =>
      mutation.mutateAsync({ kind: "retry", action, checkpoint }),
    cancel: () => mutation.mutateAsync({ kind: "cancel" }),
    pending: mutation.isPending,
    error: mutation.error,
  };
}
