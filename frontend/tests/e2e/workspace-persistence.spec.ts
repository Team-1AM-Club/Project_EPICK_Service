import { expect, test } from "@playwright/test";

test("experience and project records survive a browser reload", async ({ page }) => {
  const activities: Array<Record<string, unknown>> = [];
  const projects: Array<Record<string, unknown>> = [];

  await page.route("http://localhost:8000/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/auth/refresh") {
      return route.fulfill({ json: { access_token: "e2e-token", token_type: "Bearer", expires_in: 900 } });
    }
    if (path === "/api/v1/users/me") return route.fulfill({ json: { id: "user-1", display_name: "E2E 사용자", email: null, account_status: "ACTIVE", created_at: new Date().toISOString() } });
    if (path === "/api/v1/home") return route.fulfill({ json: { resume_items: [], projects: { in_progress_count: 0, needs_review_count: 0, recent: [] }, experience_store: { activity_count: activities.length, draft_count: activities.length }, notifications: { unread_count: 0, critical_count: 0 }, running_job_count: 0 } });
    if (path === "/api/v1/resume-items") return route.fulfill({ json: { items: [], next_cursor: null } });
    if (path === "/api/v1/activities" && request.method() === "GET") return route.fulfill({ json: { items: activities, next_cursor: null } });
    if (path === "/api/v1/activities" && request.method() === "POST") {
      const body = request.postDataJSON();
      activities.push({ id: "activity-e2e", title: body.title, activity_type: body.activity_type, organization_display: null, period_display: null, current_version: 1, episode_count: 1, registration_status: "DRAFT", usage_enabled: true, updated_at: new Date().toISOString() });
      return route.fulfill({ status: 201, json: { ...body, id: "activity-e2e", current_version: 1, registration_status: "DRAFT", created_at: new Date().toISOString(), updated_at: new Date().toISOString() } });
    }
    if (path.endsWith("/episodes") && request.method() === "POST") return route.fulfill({ status: 201, json: { ...request.postDataJSON(), id: "episode-e2e", activity_id: "activity-e2e", current_version: 1, registration_status: "DRAFT", created_at: new Date().toISOString(), updated_at: new Date().toISOString() } });
    if (path === "/api/v1/application-projects" && request.method() === "GET") return route.fulfill({ json: { items: projects, next_cursor: null } });
    if (path === "/api/v1/application-projects" && request.method() === "POST") {
      const body = request.postDataJSON();
      const created = { ...body, id: "project-e2e", organization_name: "EPICK", job_posting_id: null, current_step: null, status: "DRAFT", current_version: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
      projects.push(created);
      return route.fulfill({ status: 201, json: created });
    }
    if (path === "/api/v1/application-projects/project-e2e/questions" && request.method() === "POST") return route.fulfill({ status: 201, json: { ...request.postDataJSON(), id: "question-e2e", project_id: "project-e2e", status: "ACTIVE", current_version: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString() } });
    if (path === "/api/v1/companies") return route.fulfill({ json: { items: [{ id: "company-e2e", display_name: "EPICK", official_domain: null, identification_status: "VERIFIED" }], next_cursor: null } });
    return route.fulfill({ status: 404, json: { error: { code: "RESOURCE_NOT_FOUND", message_ko: "없음", retryable: false, actions: [], correlation_id: "e2e", fields: [] } } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "내 경험" }).click();
  await page.getByRole("button", { name: "새 경험 기록" }).click();
  await page.getByLabel("활동명").fill("새로고침 후에도 남는 경험");
  await page.getByLabel("내 역할").fill("개발");
  await page.getByRole("button", { name: "임시 저장" }).click();
  await page.reload();
  await page.getByRole("button", { name: "내 경험" }).click();
  await expect(page.getByText("새로고침 후에도 남는 경험")).toBeVisible();

  await page.getByRole("button", { name: "지원 작업" }).click();
  await page.getByRole("button", { name: "새 지원 프로젝트" }).click();
  await page.getByRole("combobox", { name: "기업" }).selectOption("company-e2e");
  await page.getByLabel("지원 직무").fill("Platform Engineer");
  await page.getByLabel("자기소개서 문항").fill("협업 경험을 설명해 주세요.");
  await page.getByRole("button", { name: /프로젝트 만들기/ }).click();
  await page.reload();
  await page.getByRole("button", { name: "지원 작업" }).click();
  await expect(page.getByText("EPICK Platform Engineer")).toBeVisible();
});
