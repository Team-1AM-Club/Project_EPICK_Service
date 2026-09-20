import type { Job } from "@/lib/api/jobs";

const actionId = "00000000-0000-4000-8000-000000000302";

export function makeJob(overrides: Partial<Job> = {}): Job {
  const status = overrides.status ?? "WAITING_USER";
  const requiredActions = overrides.required_actions ?? (
    status === "WAITING_USER"
      ? [{
          id: actionId,
          code: "CONTINUE_LIMITED" as const,
          status: "OPEN" as const,
          context_code: "MISSING_OPTIONAL_SOURCE",
          expected_input_version: "input-v1",
          expected_result_version: "result-v1",
        }]
      : status === "PAUSED_RATE_LIMIT" || status === "FAILED_RETRYABLE"
        ? [{
            id: actionId,
            code: "RETRY" as const,
            status: "OPEN" as const,
            context_code: "RETRY_ALLOWED",
            expected_input_version: "input-v1",
            expected_result_version: "result-v1",
          }]
        : []
  );
  return {
    id: "00000000-0000-4000-8000-000000000301",
    job_type: "QUESTION_ANALYSIS",
    status,
    completeness: "partial",
    dispatch_status: status === "WAITING_USER" || status === "PAUSED_RATE_LIMIT" ? "BLOCKED" : "CLAIMED",
    stage: "COLLECTING_SOURCES",
    input_refs: [],
    progress: { completed_units: 1, total_units: 4, percent: 25 },
    checkpoint: {
      available: true,
      last_completed_stage: "COLLECTING_SOURCES",
      analysis_input_version: "input-v1",
    },
    failure: { code: null, message: null, retryable: false, retry_after_seconds: null },
    limitations: [],
    created_at: "2026-09-20T00:00:00Z",
    updated_at: "2026-09-20T00:00:03Z",
    ...overrides,
    required_actions: requiredActions,
  };
}
