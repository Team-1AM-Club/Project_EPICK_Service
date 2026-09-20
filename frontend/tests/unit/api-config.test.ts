import { describe, expect, it } from "vitest";

import { parsePublicRuntimeConfig } from "@/lib/api/config";

describe("parsePublicRuntimeConfig", () => {
  it("accepts an explicit localhost W1 origin and defaults", () => {
    expect(
      parsePublicRuntimeConfig({ NEXT_PUBLIC_W1_API_URL: "http://localhost:8000" }),
    ).toEqual({
      w1ApiUrl: "http://localhost:8000",
      jobPollMs: 3000,
      resultMode: "synthetic",
    });
  });

  it.each([
    "https://api.example.com/private",
    "https://api.example.com?token=secret",
    "ftp://api.example.com",
    "http://api.example.com",
  ])("rejects unsafe public API URL %s", (w1ApiUrl) => {
    expect(() =>
      parsePublicRuntimeConfig({ NEXT_PUBLIC_W1_API_URL: w1ApiUrl }),
    ).toThrow();
  });

  it("rejects invalid polling and result-mode values", () => {
    expect(() =>
      parsePublicRuntimeConfig({
        NEXT_PUBLIC_W1_API_URL: "https://api.example.com",
        NEXT_PUBLIC_EPICK_JOB_POLL_MS: "10",
      }),
    ).toThrow();
    expect(() =>
      parsePublicRuntimeConfig({
        NEXT_PUBLIC_W1_API_URL: "https://api.example.com",
        NEXT_PUBLIC_EPICK_RESULT_MODE: "engine",
      }),
    ).toThrow();
  });
});
