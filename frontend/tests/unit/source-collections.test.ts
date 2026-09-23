import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { createSourceCollectionsApi } from "@/lib/api/source-collections";
import {
  sourceCollectionBackoff,
  useStartSourceCollection,
} from "@/lib/jobs/source-collection";
import { jobQueryKeys } from "@/lib/queries/jobs";
import { projectQueryKeys } from "@/lib/queries/projects";

describe("source collection client and cache", () => {
  it("creates, replays, and reads durable progress through W1", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({ job_id: "job-1", source_id: "source-1", status: "QUEUED", replayed: false })
      .mockResolvedValueOnce({ job_id: "job-1", source_id: "source-1", status: "QUEUED", replayed: true })
      .mockResolvedValueOnce({ job_id: "job-1", source_id: "source-1", status: "RUNNING", stage: "COLLECTING", progress: { completed_units: 1, total_units: 2, percent: 50 } })
      .mockResolvedValueOnce({ job_id: "job-1", source_id: "source-1", status: "RUNNING", stage: "COLLECTING", progress: { completed_units: 1, total_units: 2, percent: 50 } });
    const api = createSourceCollectionsApi({ request });
    const body = { source_type: "OFFICIAL_URL" as const, official_url: "https://careers.example/jobs/1", purpose: "JOB_POSTING" as const };

    const first = await api.create("project-1", body, "collect-key");
    const replay = await api.create("project-1", body, "collect-key");
    const progress = await api.progress("project-1", "job-1");
    const latest = await api.latest("project-1");

    expect(first.replayed).toBe(false);
    expect(replay).toEqual({ ...first, replayed: true });
    expect(progress.progress.percent).toBe(50);
    expect(latest.job_id).toBe("job-1");
    expect(request).toHaveBeenNthCalledWith(
      1,
      "/api/v1/application-projects/project-1/source-collections",
      expect.objectContaining({ method: "POST", headers: { "Idempotency-Key": "collect-key" } }),
    );
  });

  it("uses bounded exponential polling backoff", () => {
    expect(sourceCollectionBackoff(2_000, 0)).toBe(2_000);
    expect(sourceCollectionBackoff(2_000, 3)).toBe(15_000);
    expect(sourceCollectionBackoff(10_000, 10)).toBe(15_000);
  });

  it("invalidates project and Job caches after acceptance", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    client.setQueryData(projectQueryKeys.detail("project-1"), { id: "project-1" });
    client.setQueryData(jobQueryKeys.lists(), { items: [] });
    const api = {
      create: vi.fn().mockResolvedValue({ job_id: "job-1", source_id: "source-1", status: "QUEUED", replayed: false }),
      progress: vi.fn(),
      latest: vi.fn(),
    };
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client }, children);
    const { result } = renderHook(() => useStartSourceCollection("project-1", api), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        body: { source_type: "OFFICIAL_URL", official_url: "https://careers.example/jobs/1", purpose: "JOB_POSTING" },
        key: "collect-key",
      });
    });

    await waitFor(() => {
      expect(client.getQueryState(projectQueryKeys.detail("project-1"))?.isInvalidated).toBe(true);
    });
    expect(client.getQueryData(jobQueryKeys.detail("job-1"))).toMatchObject({ id: "job-1", status: "QUEUED" });
  });

  it("surfaces W1 errors without replacing them with timer state", async () => {
    const failure = new Error("COMPANY_DOMAIN_MISMATCH");
    const api = { create: vi.fn().mockRejectedValue(failure), progress: vi.fn(), latest: vi.fn() };
    await expect(
      api.create("project-1", { source_type: "OFFICIAL_URL", official_url: "https://evil.example", purpose: "COMPANY_PROFILE" }, "key"),
    ).rejects.toBe(failure);
  });
});
