import { afterEach, describe, expect, it, vi } from "vitest";

import { createAuthClient } from "@/lib/api/auth";
import { authStore } from "@/lib/state/auth-store";

const config = {
  w1ApiUrl: "https://api.example.test",
  jobPollMs: 3000,
  resultMode: "synthetic" as const,
};

afterEach(() => {
  authStore.clear();
  vi.unstubAllGlobals();
});

describe("auth client", () => {
  it("builds a backend login URL from a safe relative return path", () => {
    const client = createAuthClient(config, authStore);
    expect(client.loginUrl("/projects/one")).toBe(
      "https://api.example.test/api/v1/auth/google/start?return_to=%2Fprojects%2Fone",
    );
    expect(() => client.loginUrl("https://evil.test/")).toThrow();
  });

  it("keeps the access token in memory and coalesces concurrent refresh", async () => {
    let resolveFetch!: (value: Response) => void;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = createAuthClient(config, authStore);

    const first = client.refresh();
    const second = client.refresh();
    resolveFetch(
      new Response(
        JSON.stringify({ access_token: "memory-token", token_type: "Bearer", expires_in: 900 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(Promise.all([first, second])).resolves.toEqual(["memory-token", "memory-token"]);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(authStore.getSnapshot().accessToken).toBe("memory-token");
    expect(window.localStorage.length).toBe(0);
  });

  it("clears memory on failed refresh and logout", async () => {
    authStore.setAccessToken("old-token", 900);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = createAuthClient(config, authStore);

    await expect(client.refresh()).resolves.toBeNull();
    expect(authStore.getSnapshot().accessToken).toBeNull();
    authStore.setAccessToken("next-token", 900);
    await client.logout();
    expect(authStore.getSnapshot().accessToken).toBeNull();
  });
});
