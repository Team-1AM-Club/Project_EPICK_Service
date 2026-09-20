import { expect, test } from "@playwright/test";

test("observes 202 dispatch execution decision and terminal state without automatic rerun", async ({ page }) => {
  // The synthetic scenario remains inside browser routing and never becomes a production route.
  let detailReads = 0;
  let actionCalls = 0;
  const base = {
    id: "job-e2e", job_type: "QUESTION_ANALYSIS", completeness: "none",
    stage: null, input_refs: [], progress: { completed_units: 0, total_units: 4, percent: 0 },
    required_actions: [], checkpoint: { available: false, last_completed_stage: null, analysis_input_version: null },
    failure: { code: null, message: null, retryable: false, retry_after_seconds: null }, limitations: [],
    created_at: "2026-09-20T00:00:00Z", updated_at: "2026-09-20T00:00:00Z",
  };
  await page.route("http://localhost:8000/api/v1/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (path === "/api/v1/auth/refresh") return route.fulfill({ json: { access_token: "e2e-token", token_type: "Bearer", expires_in: 900 } });
    if (path === "/api/v1/users/me") return route.fulfill({ json: { id: "user-1", display_name: "E2E 사용자", email: null, account_status: "ACTIVE", created_at: base.created_at } });
    if (path === "/api/v1/home") return route.fulfill({ json: { resume_items: [{ resource_type: "WAITING_USER_JOB", resource_id: "job-e2e", title: "분석 이어하기", current_step: "WAITING_USER", updated_at: base.updated_at, resume_url: "/jobs/job-e2e", blocking_reason: null }], projects: { in_progress_count: 0, needs_review_count: 0, recent: [] }, experience_store: { activity_count: 0, draft_count: 0 }, notifications: { unread_count: 0, critical_count: 0 }, running_job_count: 1 } });
    if (path === "/api/v1/resume-items") return route.fulfill({ json: { items: [], next_cursor: null } });
    if (path === "/api/v1/application-projects") return route.fulfill({ json: { items: [], next_cursor: null } });
    if (path === "/api/v1/companies") return route.fulfill({ json: { items: [], next_cursor: null } });
    if (path === "/api/v1/jobs") return route.fulfill({ json: { items: [{ ...base, status: "QUEUED", dispatch_status: "OUTBOX_PENDING", required_action_count: 0 }], next_cursor: null } });
    if (path === "/api/v1/jobs/job-e2e" && request.method() === "GET") {
      detailReads += 1;
      if (detailReads === 1) return route.abort("internetdisconnected");
      if (detailReads === 2) return route.fulfill({ json: { ...base, status: "QUEUED", dispatch_status: "OUTBOX_PENDING" } });
      if (detailReads === 3) return route.fulfill({ json: { ...base, status: "QUEUED", dispatch_status: "ENQUEUED" } });
      if (detailReads === 4) return route.fulfill({ json: { ...base, status: "RUNNING", dispatch_status: "CLAIMED", stage: "COLLECTING_SOURCES", progress: { completed_units: 1, total_units: 4, percent: 25 } } });
      return route.fulfill({ json: { ...base, status: actionCalls ? "SUCCEEDED" : "WAITING_USER", dispatch_status: actionCalls ? "CLAIMED" : "BLOCKED", completeness: actionCalls ? "complete" : "partial", required_actions: actionCalls ? [] : [{ id: "action-e2e", code: "CONTINUE_LIMITED", status: "OPEN", context_code: "MISSING_SOURCE", expected_input_version: "input-v1", expected_result_version: "result-v1" }] } });
    }
    if (path === "/api/v1/jobs/job-e2e/actions") { actionCalls += 1; return route.fulfill({ status: 202, json: { ...base, status: "QUEUED", dispatch_status: "OUTBOX_PENDING" } }); }
    return route.fulfill({ status: 404, json: { error: { code: "RESOURCE_NOT_FOUND", message_ko: "없음", retryable: false, actions: [], correlation_id: "e2e", fields: [] } } });
  });

  await page.goto("/");
  await expect(page.getByText("분석 상태를 확인하지 못했습니다.")).toBeVisible();
  await expect(page.getByText("요청 접수")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("실행 대기")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("기업 자료 수집 중")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("사용자 선택 필요")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "확보한 자료로 계속" }).dblclick();
  await expect(page.getByText("결과 확인 가능")).toBeVisible({ timeout: 10_000 });
  expect(actionCalls).toBe(1);
});
