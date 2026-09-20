import { describe, expect, it, vi } from "vitest";

import { createJobActionExecutor } from "@/lib/jobs/use-job-actions";
import { makeJob } from "../mocks/job-scenarios";

describe("Job action executor", () => {
  it("deduplicates a double click and reuses one per-intent idempotency key", async () => {
    let resolve!: (job: ReturnType<typeof makeJob>) => void;
    const action = vi.fn().mockReturnValue(new Promise((next) => { resolve = next; }));
    const executor = createJobActionExecutor(
      { action, retry: vi.fn(), cancel: vi.fn() },
      () => ({ key: "intent-1", requestFingerprint: "fingerprint" }),
    );
    const requiredAction = makeJob({ status: "WAITING_USER" }).required_actions![0];

    const first = executor.submit("job-1", requiredAction);
    const second = executor.submit("job-1", requiredAction);
    expect(action).toHaveBeenCalledOnce();
    expect(action.mock.calls[0][2]).toBe("intent-1");
    resolve(makeJob());
    await expect(Promise.all([first, second])).resolves.toHaveLength(2);
  });

  it("forwards required expected versions and checkpoint stage for retry", async () => {
    const retry = vi.fn().mockResolvedValue(makeJob({ status: "QUEUED" }));
    const executor = createJobActionExecutor(
      { action: vi.fn(), retry, cancel: vi.fn() },
      () => ({ key: "retry-intent", requestFingerprint: "fingerprint" }),
    );
    const job = makeJob({ status: "PAUSED_RATE_LIMIT" });

    await executor.retry("job-1", job.required_actions![0], job.checkpoint);

    expect(retry).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({
        required_action_id: job.required_actions![0].id,
        expected_input_version: "input-v1",
        expected_result_version: "result-v1",
        from_stage: "COLLECTING_SOURCES",
        acknowledge_rate_limit: true,
      }),
      "retry-intent",
    );
  });

  it("deduplicates cancel without adding a client-only expected-version field", async () => {
    const cancel = vi.fn().mockResolvedValue(makeJob({ status: "CANCELLED" }));
    const executor = createJobActionExecutor(
      { action: vi.fn(), retry: vi.fn(), cancel },
      () => ({ key: "cancel-intent", requestFingerprint: "fingerprint" }),
    );
    await Promise.all([executor.cancel("job-1"), executor.cancel("job-1")]);
    expect(cancel).toHaveBeenCalledOnce();
    expect(cancel).toHaveBeenCalledWith("job-1", "cancel-intent");
  });
});
