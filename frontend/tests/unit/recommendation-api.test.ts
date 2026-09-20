import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import {
  createRecommendationsApi,
  describeRecommendationError,
} from "@/lib/api/recommendations";
import { makeRecommendationRun } from "../mocks/recommendation-scenarios";

describe("recommendation API adapter", () => {
  it("creates a version-fenced run and preserves its result origin", async () => {
    const request = vi.fn().mockResolvedValue(makeRecommendationRun({ result_origin: "ENGINE" }));
    const api = createRecommendationsApi({ request });

    const result = await api.createRun(
      "question-1",
      {
        question_version: 2,
        snapshot_version: 0,
        candidate_limit: 5,
        include_excluded: false,
        allow_limited_analysis: true,
      },
      "intent-1",
    );

    expect(result.result_origin).toBe("ENGINE");
    expect(request).toHaveBeenCalledWith(
      "/api/v1/questions/question-1/recommendation-runs",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "intent-1" }),
      }),
    );
  });

  it("uses the signed cursor contract for candidate pagination and detail", async () => {
    const request = vi.fn().mockResolvedValue({ items: [], next_cursor: "signed-next" });
    const api = createRecommendationsApi({ request });
    await api.listCandidates("run-1", { cursor: "signed cursor", limit: 7 });
    await api.getCandidate("candidate-1");

    expect(request.mock.calls[0][0]).toBe(
      "/api/v1/recommendation-runs/run-1/candidates?cursor=signed+cursor&limit=7",
    );
    expect(request.mock.calls[1][0]).toBe("/api/v1/recommendation-candidates/candidate-1");
  });

  it("adapts stable error codes without branching on Korean message text", () => {
    expect(describeRecommendationError(new ApiError(409, "STALE_INPUT", "arbitrary"))).toBe(
      "입력이 변경되어 이 추천 결과를 사용할 수 없습니다.",
    );
    expect(
      describeRecommendationError(new ApiError(409, "ACTION_NOT_ALLOWED", "different")),
    ).toBe("현재 선택을 먼저 확인하거나 교체를 선택해 주세요.");
    expect(describeRecommendationError(new Error("transport"))).toBe(
      "추천 결과를 불러오지 못했습니다.",
    );
  });
});
