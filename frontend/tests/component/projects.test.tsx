import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { Projects } from "@/app/features";
import { createEpikQueryClient } from "@/lib/queries/query-client";
import { mockApiServer } from "../mocks/server";

const origin = "http://localhost:8000";

function renderProjects(activeId: string | null = null) {
  return render(
    <QueryClientProvider client={createEpikQueryClient()}>
      <Projects activeId={activeId} onActive={() => {}} onAddExperience={() => {}} />
    </QueryClientProvider>,
  );
}

describe("Projects", () => {
  it("creates a project and its questions through W1 public APIs", async () => {
    const user = userEvent.setup();
    let createdProject = false;
    mockApiServer.use(
      http.get(`${origin}/api/v1/application-projects`, () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
      http.get(`${origin}/api/v1/companies`, () =>
        HttpResponse.json({
          items: [{ id: "company-1", display_name: "EPICK", official_domain: "epick.test", identification_status: "VERIFIED" }],
          next_cursor: null,
        }),
      ),
      http.post(`${origin}/api/v1/application-projects`, async ({ request }) => {
        createdProject = true;
        const body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ...body,
          id: "project-1",
          organization_name: "EPICK",
          job_posting_id: null,
          current_step: null,
          status: "DRAFT",
          current_version: 1,
          created_at: "2026-09-20T00:00:00Z",
          updated_at: "2026-09-20T00:00:00Z",
        }, { status: 201 });
      }),
      http.post(`${origin}/api/v1/application-projects/project-1/questions`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          ...body,
          id: "question-1",
          project_id: "project-1",
          status: "ACTIVE",
          current_version: 1,
          created_at: "2026-09-20T00:00:00Z",
          updated_at: "2026-09-20T00:00:00Z",
        }, { status: 201 });
      }),
    );

    renderProjects();
    await user.click(await screen.findByRole("button", { name: "새 지원 프로젝트" }));
    await user.selectOptions(screen.getByLabelText("기업"), "company-1");
    await user.type(screen.getByLabelText("지원 직무"), "Platform Engineer");
    await user.type(screen.getByLabelText("자기소개서 문항"), "협업 경험을 설명해 주세요.");
    await user.click(screen.getByRole("button", { name: /프로젝트 만들기/ }));

    expect(createdProject).toBe(true);
    expect(window.localStorage.length).toBe(0);
  });

  it("shows a safe reload action for a stale project update", async () => {
    mockApiServer.use(
      http.get(`${origin}/api/v1/application-projects/project-1`, () =>
        HttpResponse.json({
          id: "project-1", company_id: "company-1", title: "EPICK 지원", role_name: "Backend",
          organization_name: "EPICK", season: null, job_posting_id: null, current_step: null,
          status: "DRAFT", current_version: 2, created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-20T00:00:00Z",
        }),
      ),
      http.get(`${origin}/api/v1/application-projects/project-1/questions`, () => HttpResponse.json([])),
      http.patch(`${origin}/api/v1/application-projects/project-1`, () =>
        HttpResponse.json({ error: { code: "VERSION_CONFLICT", message_ko: "다른 변경이 있습니다.", retryable: false, actions: [], correlation_id: "corr", fields: [] } }, { status: 412 }),
      ),
    );
    const user = userEvent.setup();
    renderProjects("project-1");
    await user.click(await screen.findByRole("button", { name: "프로젝트 수정" }));
    await user.clear(screen.getByLabelText("지원 직무"));
    await user.type(screen.getByLabelText("지원 직무"), "Platform Engineer");
    await user.click(screen.getByRole("button", { name: "변경 저장" }));
    expect(await screen.findByRole("button", { name: "서버 내용 다시 불러오기" })).toBeVisible();
  });
});
