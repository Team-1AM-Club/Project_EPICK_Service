import { describe, expect, it, vi } from "vitest";

import { createActivitiesApi } from "@/lib/api/activities";
import {
  ExperienceSaveError,
  persistExperience,
  toActivityCreate,
  toEpisodeCreate,
  type ExperienceFormValue,
} from "@/lib/api/experience-adapter";

const form: ExperienceFormValue = {
  title: "EPICK 연동",
  organization: "",
  activityType: "PROJECT",
  startDate: "2026-09-01",
  endDate: "2026-09-20",
  role: "백엔드 개발",
  outcomeStatus: "IN_PROGRESS",
  outcomeSummary: "PostgreSQL 저장",
  story: "FastAPI 공개 계약을 통해 저장했다.",
};

describe("experience API mapping", () => {
  it("preserves explicit field availability instead of inventing missing values", () => {
    expect(toActivityCreate(form)).toMatchObject({
      title: "EPICK 연동",
      organization: { value: null, availability: "NOT_PROVIDED" },
      role: { value: "백엔드 개발", availability: "PROVIDED" },
      period: {
        start_date: "2026-09-01",
        end_date: "2026-09-20",
        precision: "DAY",
        availability: "PROVIDED",
      },
    });
    expect(toEpisodeCreate(form)).toMatchObject({
      title: "EPICK 연동",
      original_narrative: "FastAPI 공개 계약을 통해 저장했다.",
    });
  });

  it("sends idempotency and version headers through the typed activity client", async () => {
    const request = vi.fn().mockResolvedValue({ id: "activity-1", current_version: 2 });
    const api = createActivitiesApi({ request });

    await api.update("activity-1", 1, { title: "수정", change_reason: "사용자 수정" }, "key-1");
    await api.list({ q: "EPICK", status: "DRAFT", cursor: "next" });

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/api/v1/activities/activity-1",
      expect.objectContaining({
        method: "PATCH",
        headers: { "Idempotency-Key": "key-1", "If-Match": '"1"' },
      }),
    );
    expect(request.mock.calls[1][0]).toBe(
      "/api/v1/activities?q=EPICK&status=DRAFT&cursor=next",
    );
  });

  it("keeps the Activity draft recoverable when the second Episode write fails", async () => {
    const activities = {
      create: vi.fn().mockResolvedValue({ id: "activity-1", current_version: 1 }),
      update: vi.fn(),
      complete: vi.fn(),
    };
    const episodes = {
      create: vi.fn().mockRejectedValue(new Error("episode unavailable")),
      update: vi.fn(),
      complete: vi.fn(),
    };

    await expect(
      persistExperience({ form, mode: "draft", activities, episodes, makeKey: () => "stable" }),
    ).rejects.toMatchObject({ activityId: "activity-1", phase: "episode" } satisfies Partial<ExperienceSaveError>);
    expect(activities.create).toHaveBeenCalledOnce();
    expect(episodes.create).toHaveBeenCalledWith(
      "activity-1",
      expect.objectContaining({ title: form.title }),
      "stable",
    );
    expect(activities.complete).not.toHaveBeenCalled();
  });
});
