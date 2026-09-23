import { expect, test } from "@playwright/test";

test("submits an official URL and restores durable progress without duplicate registration", async ({ page }) => {
  let submissions = 0;
  const idempotencyKeys: string[] = [];
  const createdAt = "2026-09-23T00:00:00Z";
  const jobBase = {
    id: "job-source-1",
    job_type: "SOURCE_REGISTRATION",
    status: "QUEUED",
    completeness: "none",
    dispatch_status: "OUTBOX_PENDING",
    stage: null,
    input_refs: [],
    progress: { completed_units: 0, total_units: null, percent: null },
    required_actions: [],
    checkpoint: { available: false, last_completed_stage: null, analysis_input_version: null },
    failure: { code: null, message: null, retryable: false, retry_after_seconds: null },
    limitations: [],
    created_at: createdAt,
    updated_at: createdAt,
  };

  await page.route("http://localhost:8000/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/auth/refresh") {
      return route.fulfill({ json: { access_token: "source-e2e-token", token_type: "Bearer", expires_in: 900 } });
    }
    if (path === "/api/v1/users/me") {
      return route.fulfill({ json: { id: "user-1", display_name: "E2E 사용자", email: null, account_status: "ACTIVE", created_at: createdAt } });
    }
    if (path === "/api/v1/home") {
      return route.fulfill({ json: { resume_items: [], projects: { in_progress_count: 1, needs_review_count: 0, recent: [] }, experience_store: { activity_count: 0, draft_count: 0 }, notifications: { unread_count: 0, critical_count: 0 }, running_job_count: submissions ? 1 : 0 } });
    }
    if (path === "/api/v1/resume-items") return route.fulfill({ json: { items: [], next_cursor: null } });
    if (path === "/api/v1/companies") {
      return route.fulfill({ json: { items: [{ id: "company-1", display_name: "Example", official_domain: "careers.example.com", identification_status: "VERIFIED" }], next_cursor: null } });
    }
    if (path === "/api/v1/application-projects" && request.method() === "GET") {
      return route.fulfill({ json: { items: [{ id: "project-1", company_id: "company-1", title: "Example 지원", status: "DRAFT", current_step: null, current_version: 1, updated_at: createdAt }], next_cursor: null } });
    }
    if (path === "/api/v1/jobs") {
      return route.fulfill({ json: { items: submissions ? [{ ...jobBase, required_action_count: 0 }] : [], next_cursor: null } });
    }
    if (path === "/api/v1/jobs/job-source-1") {
      return route.fulfill({ json: { ...jobBase, status: submissions > 1 ? "RUNNING" : "QUEUED", dispatch_status: submissions > 1 ? "CLAIMED" : "OUTBOX_PENDING", stage: submissions > 1 ? "COLLECTING" : null } });
    }
    if (path === "/api/v1/application-projects/project-1/source-collections" && request.method() === "GET") {
      if (!submissions) return route.fulfill({ status: 404, json: { error: { code: "RESOURCE_NOT_FOUND", message_ko: "없음", retryable: false, actions: [], correlation_id: "e2e", fields: [] } } });
      return route.fulfill({ json: { job_id: "job-source-1", source_id: "source-1", status: submissions > 1 ? "RUNNING" : "QUEUED", stage: submissions > 1 ? "COLLECTING" : null, progress: { completed_units: 0, total_units: null, percent: null } } });
    }
    if (path === "/api/v1/application-projects/project-1/source-collections" && request.method() === "POST") {
      submissions += 1;
      idempotencyKeys.push(request.headers()["idempotency-key"]);
      return route.fulfill({
        status: 202,
        headers: { Location: "/api/v1/jobs/job-source-1" },
        json: { job_id: "job-source-1", source_id: "source-1", status: "QUEUED", replayed: submissions > 1 },
      });
    }
    return route.fulfill({ status: 404, json: { error: { code: "RESOURCE_NOT_FOUND", message_ko: "없음", retryable: false, actions: [], correlation_id: "e2e", fields: [] } } });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Example" })).toBeVisible();
  await page.getByRole("button", { name: "공식 URL 수집" }).click();
  await page.getByRole("textbox", { name: "공식 URL" }).fill("https://careers.example.com/jobs/42");
  await page.getByRole("combobox", { name: "자료 종류" }).selectOption("JOB_POSTING");
  await page.getByRole("button", { name: "수집 시작" }).click();
  await expect(page.getByText(/수집 상태: QUEUED/)).toBeVisible();

  await page.getByRole("button", { name: "공식 URL 수집" }).click();
  await page.getByRole("button", { name: "수집 시작" }).click();
  await expect(page.getByText(/수집 상태: RUNNING/)).toBeVisible();
  expect(submissions).toBe(2);
  expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);

  await page.reload();
  await expect(page.getByText(/수집 상태: RUNNING/)).toBeVisible();
});
