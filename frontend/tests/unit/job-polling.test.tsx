import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Job } from "@/lib/api/jobs";
import { useJobPolling } from "@/lib/jobs/use-job-polling";
import { createEpikQueryClient } from "@/lib/queries/query-client";
import { makeJob } from "../mocks/job-scenarios";

function Harness({ fetchJob, pollMs = 1000 }: { fetchJob: () => Promise<Job>; pollMs?: number }) {
  const query = useJobPolling("job-1", { fetchJob, pollMs });
  return <div>{query.error ? "관찰 오류" : query.data?.status ?? "loading"}</div>;
}

function mount(fetchJob: () => Promise<Job>, pollMs = 1000) {
  return render(<QueryClientProvider client={createEpikQueryClient()}><Harness fetchJob={fetchJob} pollMs={pollMs} /></QueryClientProvider>);
}

function setVisible(visible: boolean) {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: visible ? "visible" : "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
}

function setOnline(online: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, value: online });
  window.dispatchEvent(new Event(online ? "online" : "offline"));
}

afterEach(() => {
  vi.useRealTimers();
  setVisible(true);
  setOnline(true);
});

describe("useJobPolling", () => {
  it("polls active jobs and stops for waiting and terminal states", async () => {
    vi.useFakeTimers();
    const fetchJob = vi.fn()
      .mockResolvedValueOnce(makeJob({ status: "QUEUED" }))
      .mockResolvedValueOnce(makeJob({ status: "WAITING_USER" }));
    mount(fetchJob);
    await act(async () => { await vi.runOnlyPendingTimersAsync(); });
    expect(fetchJob).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchJob).toHaveBeenCalledTimes(2);
  });

  it("pauses while hidden/offline and immediately reads current state on resume", async () => {
    setVisible(false);
    setOnline(false);
    const fetchJob = vi.fn().mockResolvedValue(makeJob());
    mount(fetchJob);
    expect(fetchJob).not.toHaveBeenCalled();
    act(() => { setVisible(true); setOnline(true); });
    await waitFor(() => expect(fetchJob).toHaveBeenCalledOnce());
  });

  it("backs observation failures off without mutating or rerunning a Job", async () => {
    vi.useFakeTimers();
    const fetchJob = vi.fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValue(makeJob({ status: "RUNNING" }));
    mount(fetchJob, 1000);
    await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
    expect(fetchJob).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetchJob).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("FAILED_RETRYABLE")).not.toBeInTheDocument();
  });

  it("waits for the server retry-after window before one current-state read", async () => {
    vi.useFakeTimers();
    const fetchJob = vi.fn()
      .mockResolvedValueOnce(makeJob({
        status: "PAUSED_RATE_LIMIT",
        failure: { code: "RATE_LIMIT", message: null, retryable: true, retry_after_seconds: 2 },
      }))
      .mockResolvedValueOnce(makeJob({ status: "WAITING_USER" }));
    mount(fetchJob, 1000);
    await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
    expect(fetchJob).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(fetchJob).toHaveBeenCalledTimes(2);
  });
});
