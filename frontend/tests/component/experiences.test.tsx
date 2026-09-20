import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { Experiences } from "@/app/features";
import { createEpikQueryClient } from "@/lib/queries/query-client";
import { mockApiServer } from "../mocks/server";

const origin = "http://localhost:8000";

function renderExperiences(resumeActivityId?: string) {
  return render(
    <QueryClientProvider client={createEpikQueryClient()}>
      <Experiences resumeActivityId={resumeActivityId} />
    </QueryClientProvider>,
  );
}

describe("Experiences", () => {
  it("creates a server-backed draft without writing personal data to localStorage", async () => {
    const user = userEvent.setup();
    const items: Array<Record<string, unknown>> = [];
    mockApiServer.use(
      http.get(`${origin}/api/v1/activities`, () =>
        HttpResponse.json({ items, next_cursor: null }),
      ),
      http.post(`${origin}/api/v1/activities`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        const value = {
          ...body,
          id: "activity-1",
          current_version: 1,
          registration_status: "DRAFT",
          created_at: "2026-09-20T00:00:00Z",
          updated_at: "2026-09-20T00:00:00Z",
        };
        items.push({
          id: value.id,
          title: body.title,
          activity_type: body.activity_type,
          organization_display: null,
          period_display: null,
          current_version: 1,
          episode_count: 1,
          registration_status: "DRAFT",
          usage_enabled: true,
          updated_at: value.updated_at,
        });
        return HttpResponse.json(value, { status: 201 });
      }),
      http.post(`${origin}/api/v1/activities/activity-1/episodes`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(
          {
            ...body,
            id: "episode-1",
            activity_id: "activity-1",
            current_version: 1,
            registration_status: "DRAFT",
            created_at: "2026-09-20T00:00:00Z",
            updated_at: "2026-09-20T00:00:00Z",
          },
          { status: 201 },
        );
      }),
    );

    renderExperiences();
    await user.click(await screen.findByRole("button", { name: "새 경험 기록" }));
    await user.type(screen.getByLabelText("활동명"), "서버에 남는 경험");
    await user.type(screen.getByLabelText("내 역할"), "개발");
    await user.click(screen.getByRole("button", { name: "임시 저장" }));

    expect(await screen.findByText("서버에 남는 경험")).toBeInTheDocument();
    expect(window.localStorage.length).toBe(0);
  });

  it("completes the initial Episode and Activity after saving required fields", async () => {
    const user = userEvent.setup();
    let activityCompleted = false;
    let episodeCompleted = false;
    mockApiServer.use(
      http.get(`${origin}/api/v1/activities`, () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
      http.post(`${origin}/api/v1/activities`, async ({ request }) =>
        HttpResponse.json({
          ...((await request.json()) as object), id: "activity-complete", current_version: 1,
          registration_status: "DRAFT", created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-20T00:00:00Z",
        }, { status: 201 }),
      ),
      http.post(`${origin}/api/v1/activities/activity-complete/episodes`, async ({ request }) =>
        HttpResponse.json({
          ...((await request.json()) as object), id: "episode-complete", activity_id: "activity-complete",
          current_version: 1, registration_status: "DRAFT", created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-20T00:00:00Z",
        }, { status: 201 }),
      ),
      http.post(`${origin}/api/v1/episodes/episode-complete/complete`, () => {
        episodeCompleted = true;
        return HttpResponse.json({ id: "episode-complete", activity_id: "activity-complete", current_version: 2 });
      }),
      http.post(`${origin}/api/v1/activities/activity-complete/complete`, () => {
        activityCompleted = true;
        return HttpResponse.json({ id: "activity-complete", current_version: 2 });
      }),
    );

    renderExperiences();
    await user.click(await screen.findByRole("button", { name: "새 경험 기록" }));
    await user.type(screen.getByLabelText("활동명"), "완료할 경험");
    await user.type(screen.getByLabelText("소속 · 주관기관"), "EPICK");
    await user.type(screen.getByLabelText("시작일"), "2026-09-01");
    await user.type(screen.getByLabelText("내 역할"), "개발");
    await user.click(screen.getByRole("button", { name: /경험 저장/ }));

    await waitFor(() => expect(activityCompleted).toBe(true));
    expect(episodeCompleted).toBe(true);
  });

  it("opens a resume Activity from the server", async () => {
    mockApiServer.use(
      http.get(`${origin}/api/v1/activities`, () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
      http.get(`${origin}/api/v1/activities/activity-resume`, () =>
        HttpResponse.json({
          id: "activity-resume",
          title: "이어 쓸 경험",
          activity_type: "PROJECT",
          organization: { value: null, availability: "NOT_PROVIDED" },
          period: { availability: "NOT_PROVIDED", start_date: null, end_date: null, precision: null },
          role: { value: "개발", availability: "PROVIDED" },
          outcome: { status: "IN_PROGRESS", summary: { value: null, availability: "NOT_PROVIDED" } },
          original_narrative: null,
          usage_enabled: true,
          registration_status: "DRAFT",
          current_version: 1,
          created_at: "2026-09-20T00:00:00Z",
          updated_at: "2026-09-20T00:00:00Z",
        }),
      ),
      http.get(`${origin}/api/v1/activities/activity-resume/episodes`, () => HttpResponse.json([])),
    );

    renderExperiences("activity-resume");
    await waitFor(() => expect(screen.getByLabelText("활동명")).toHaveValue("이어 쓸 경험"));
  });
});
