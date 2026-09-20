import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { classifyConflict } from "@/lib/api/conflicts";
import { createProjectsApi } from "@/lib/api/projects";
import { createQuestionsApi } from "@/lib/api/questions";

describe("project and question API clients", () => {
  it("encodes pagination and keeps version/idempotency headers on project writes", async () => {
    const request = vi.fn().mockResolvedValue({ items: [], next_cursor: null });
    const api = createProjectsApi({ request });

    await api.list({ cursor: "cursor-1", limit: 20 });
    await api.update(
      "project-1",
      4,
      { role_name: "Platform Engineer", change_reason: "직무 수정" },
      "project-key",
    );

    expect(request.mock.calls[0][0]).toBe(
      "/api/v1/application-projects?cursor=cursor-1&limit=20",
    );
    expect(request).toHaveBeenNthCalledWith(
      2,
      "/api/v1/application-projects/project-1",
      expect.objectContaining({
        method: "PATCH",
        headers: { "Idempotency-Key": "project-key", "If-Match": '"4"' },
      }),
    );
  });

  it("uses the same version contract for question update and archive", async () => {
    const request = vi.fn().mockResolvedValue(undefined);
    const api = createQuestionsApi({ request });

    await api.update("question-1", 2, { prompt: "수정 문항" }, "question-key");
    await api.archive("question-1", 3, "archive-key");

    expect(request.mock.calls[0][1].headers).toEqual({
      "Idempotency-Key": "question-key",
      "If-Match": '"2"',
    });
    expect(request.mock.calls[1][1].headers).toEqual({
      "Idempotency-Key": "archive-key",
      "If-Match": '"3"',
    });
  });

  it.each([409, 412])("classifies %i as a reloadable optimistic conflict", (status) => {
    const error = new ApiError(status, "VERSION_CONFLICT", "다른 변경이 있습니다.");
    expect(classifyConflict(error)).toEqual({
      kind: "version-conflict",
      message: "다른 변경이 있습니다.",
      canReload: true,
    });
  });
});
