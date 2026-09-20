import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, createApiClient } from "@/lib/api/client";

const config = {
  w1ApiUrl: "https://api.example.com",
  jobPollMs: 3000,
  resultMode: "synthetic" as const,
};

afterEach(() => vi.unstubAllGlobals());

describe("W1 API client", () => {
  it("injects the in-memory bearer and forwards AbortSignal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "user-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const client = createApiClient({ config, getAccessToken: () => "memory-token" });

    await expect(
      client.request<{ id: string }>("/api/v1/users/me", { signal: controller.signal }),
    ).resolves.toEqual({ id: "user-1" });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://api.example.com/api/v1/users/me",
      expect.objectContaining({
        signal: controller.signal,
        headers: expect.any(Headers),
      }),
    );
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer memory-token");
  });

  it("decodes the public error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "RESOURCE_NOT_FOUND",
              message_ko: "찾을 수 없습니다.",
              retryable: false,
              actions: [],
              correlation_id: "corr-1",
              fields: [],
            },
          }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const client = createApiClient({ config, getAccessToken: () => null });

    await expect(client.request("/api/v1/users/missing")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      code: "RESOURCE_NOT_FOUND",
      correlationId: "corr-1",
    } satisfies Partial<ApiError>);
  });

  it("refreshes and retries at most once after 401", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    const refreshAccessToken = vi.fn().mockResolvedValue("new-token");
    const client = createApiClient({
      config,
      getAccessToken: () => "old-token",
      refreshAccessToken,
    });

    await expect(client.request("/api/v1/users/me")).rejects.toMatchObject({ status: 401 });
    expect(refreshAccessToken).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    "https://w2.internal/private",
    "//neo4j.internal/query",
    "/private/w3",
  ])("forbids non-public destinations: %s", async (path) => {
    const client = createApiClient({ config, getAccessToken: () => null });
    await expect(client.request(path)).rejects.toThrow("W1 public API path");
  });
});
