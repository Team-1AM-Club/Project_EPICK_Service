import { expect, test } from "@playwright/test";

test("compares selects reloads and rejects a stale recommendation result", async ({ page }) => {
  let selection: Record<string, unknown> | null = null;
  let selectionWrites = 0;
  let questionVersion = 2;
  const now = "2026-09-20T00:00:00Z";
  const run = {
    id: "run-e2e", project_id: "project-e2e", question_id: "question-e2e",
    question_version: 2, snapshot_id: "snapshot-e2e", snapshot_version: 1,
    status: "SUCCEEDED", result_status: "READY", result_origin: "SYNTHETIC",
    requested_candidate_limit: 2, limited_analysis: false, limitations: [],
    candidates_url: "/api/v1/recommendation-runs/run-e2e/candidates",
    created_at: now, completed_at: now,
  };
  const candidates = [
    { id: "candidate-1", candidate_no: 1, episode_version_id: "episode-version-1", match_status: "DIRECT_MATCH", short_reason: "협업 문제를 조율한 경험", strength_summary: "행동과 결과가 구체적입니다.", limitation_summary: null, validation_status: "PASSED", result_version: "result-v2" },
    { id: "candidate-2", candidate_no: 2, episode_version_id: "episode-version-2", match_status: "PARTIAL_RELEVANCE", short_reason: "일부 역량이 연결되는 경험", strength_summary: "역할이 명확합니다.", limitation_summary: "직무 맥락 확인 필요", validation_status: "LIMITED", result_version: "result-v2" },
  ];

  await page.route("http://localhost:8000/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const notFound = { error: { code: "RESOURCE_NOT_FOUND", message_ko: "없음", retryable: false, actions: [], correlation_id: "e2e", fields: [] } };
    if (path === "/api/v1/auth/refresh") return route.fulfill({ json: { access_token: "e2e-token", token_type: "Bearer", expires_in: 900 } });
    if (path === "/api/v1/users/me") return route.fulfill({ json: { id: "user-1", display_name: "E2E 사용자", email: null, account_status: "ACTIVE", created_at: now } });
    if (path === "/api/v1/home") return route.fulfill({ json: { resume_items: [{ resource_type: "PROJECT", resource_id: "project-e2e", title: "지원 작업", current_step: "추천 확인", updated_at: now, resume_url: "/application-projects/project-e2e", blocking_reason: null }], projects: { in_progress_count: 1, needs_review_count: 0, recent: [] }, experience_store: { activity_count: 2, draft_count: 0 }, notifications: { unread_count: 0, critical_count: 0 }, running_job_count: 0 } });
    if (path === "/api/v1/resume-items") return route.fulfill({ json: { items: [], next_cursor: null } });
    if (path === "/api/v1/application-projects") return route.fulfill({ json: { items: [{ id: "project-e2e", title: "EPICK Backend Engineer", organization_name: "EPICK", role_name: "Backend Engineer", status: "READY", current_step: "추천 확인", current_version: 1, updated_at: now }], next_cursor: null } });
    if (path === "/api/v1/application-projects/project-e2e") return route.fulfill({ json: { id: "project-e2e", title: "EPICK Backend Engineer", company_id: "company-e2e", organization_name: "EPICK", role_name: "Backend Engineer", job_posting_id: null, current_step: "추천 확인", status: "READY", current_version: 1, created_at: now, updated_at: now } });
    if (path === "/api/v1/application-projects/project-e2e/questions") return route.fulfill({ json: [{ id: "question-e2e", current_version: questionVersion, prompt: "협업 경험을 설명해 주세요.", character_limit: null, display_order: 0, source: "USER_INPUT", updated_at: now }] });
    if (path === "/api/v1/companies") return route.fulfill({ json: { items: [], next_cursor: null } });
    if (path === "/api/v1/jobs") return route.fulfill({ json: { items: [], next_cursor: null } });
    if (path === "/api/v1/questions/question-e2e/selection" && request.method() === "GET") return selection ? route.fulfill({ json: selection }) : route.fulfill({ status: 404, json: notFound });
    if (path === "/api/v1/questions/question-e2e/recommendation-runs" && request.method() === "POST") return route.fulfill({ status: 202, json: run });
    if (path === "/api/v1/recommendation-runs/run-e2e") return route.fulfill({ json: run });
    if (path === "/api/v1/recommendation-runs/run-e2e/candidates") return route.fulfill({ json: { items: candidates, next_cursor: null } });
    if (path === "/api/v1/recommendation-candidates/candidate-1/select") {
      selectionWrites += 1;
      selection = { selection_id: "selection-e2e", project_id: "project-e2e", question_id: "question-e2e", recommendation_run_id: "run-e2e", candidate_id: "candidate-1", episode_ref: { episode_id: "episode-1", version: 1 }, selected_at: now, warnings: [] };
      return route.fulfill({ status: 201, json: selection });
    }
    return route.fulfill({ status: 404, json: notFound });
  });

  await page.goto("/");
  await page.getByRole("button", { name: /QUESTION 01/ }).click();
  await page.getByRole("button", { name: "추천 분석 시작" }).click();
  await expect(page.getByText("합성 검증 결과")).toBeVisible();
  await page.getByLabel("협업 문제를 조율한 경험 비교").check();
  await page.getByLabel("일부 역량이 연결되는 경험 비교").check();
  await expect(page.getByRole("button", { name: "선택한 2개 비교" })).toBeVisible();
  await page.getByRole("button", { name: "소재로 선택" }).first().dblclick();
  expect(selectionWrites).toBe(1);

  await page.reload();
  await page.getByRole("button", { name: /QUESTION 01/ }).click();
  await expect(page.getByText("선택한 소재")).toBeVisible();

  questionVersion = 3;
  await page.reload();
  await page.getByRole("button", { name: /QUESTION 01/ }).click();
  await expect(page.getByText("문항이 변경되어 이전 추천을 선택할 수 없습니다.")).toBeVisible();
  await expect(page.getByRole("button", { name: "소재로 선택" }).first()).toBeDisabled();
});
