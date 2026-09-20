import { expect, test } from "@playwright/test";

test("uses unchanged W1 endpoints for an ENGINE limited recommendation", async ({ page }) => {
  const now = "2026-09-20T00:00:00Z";
  const privateDestinations: string[] = [];
  let selection: Record<string, unknown> | null = null;
  const run = {
    id: "w4-run-e2e",
    project_id: "project-e2e",
    question_id: "question-e2e",
    question_version: 2,
    snapshot_id: "snapshot-e2e",
    snapshot_version: 1,
    status: "LIMITED",
    result_status: "LIMITED",
    result_origin: "ENGINE",
    requested_candidate_limit: 1,
    limited_analysis: true,
    limitations: ["W4_SYNTHETIC_ACCEPTANCE_ONLY"],
    candidates_url: "/api/v1/recommendation-runs/w4-run-e2e/candidates",
    created_at: now,
    completed_at: now,
  };
  const candidate = {
    id: "w4-candidate-1",
    candidate_no: 1,
    episode_version_id: "episode-version-1",
    match_status: "DIRECT_MATCH",
    short_reason: "W4가 제한된 합성 입력에서 추천한 경험",
    strength_summary: "행동과 결과가 구체적입니다.",
    limitation_summary: "합성 수락 시험 결과입니다.",
    validation_status: "LIMITED",
    result_version: "w4-result-v1",
  };

  page.on("request", (request) => {
    const url = request.url();
    const parsed = new URL(url);
    if (
      parsed.pathname.startsWith("/internal/") ||
      parsed.hostname.startsWith("sqs.") ||
      /w4-private|lease_token/i.test(parsed.search)
    ) {
      privateDestinations.push(url);
    }
  });

  await page.route("http://localhost:8000/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const notFound = {
      error: {
        code: "RESOURCE_NOT_FOUND",
        message_ko: "없음",
        retryable: false,
        actions: [],
        correlation_id: "w4-e2e",
        fields: [],
      },
    };
    if (path === "/api/v1/auth/refresh") {
      return route.fulfill({
        json: { access_token: "w4-e2e-token", token_type: "Bearer", expires_in: 900 },
      });
    }
    if (path === "/api/v1/users/me") {
      return route.fulfill({
        json: {
          id: "user-1",
          display_name: "E2E 사용자",
          email: null,
          account_status: "ACTIVE",
          created_at: now,
        },
      });
    }
    if (path === "/api/v1/home") {
      return route.fulfill({
        json: {
          resume_items: [],
          projects: { in_progress_count: 1, needs_review_count: 0, recent: [] },
          experience_store: { activity_count: 1, draft_count: 0 },
          notifications: { unread_count: 0, critical_count: 0 },
          running_job_count: 0,
        },
      });
    }
    if (path === "/api/v1/resume-items") {
      return route.fulfill({ json: { items: [], next_cursor: null } });
    }
    if (path === "/api/v1/application-projects") {
      return route.fulfill({
        json: {
          items: [
            {
              id: "project-e2e",
              title: "W4 통합 검증",
              organization_name: "EPICK",
              role_name: "Backend Engineer",
              status: "READY",
              current_step: "추천 확인",
              current_version: 1,
              updated_at: now,
            },
          ],
          next_cursor: null,
        },
      });
    }
    if (path === "/api/v1/application-projects/project-e2e") {
      return route.fulfill({
        json: {
          id: "project-e2e",
          title: "W4 통합 검증",
          company_id: "company-e2e",
          organization_name: "EPICK",
          role_name: "Backend Engineer",
          job_posting_id: null,
          current_step: "추천 확인",
          status: "READY",
          current_version: 1,
          created_at: now,
          updated_at: now,
        },
      });
    }
    if (path === "/api/v1/application-projects/project-e2e/questions") {
      return route.fulfill({
        json: [
          {
            id: "question-e2e",
            current_version: 2,
            prompt: "협업 경험을 설명해 주세요.",
            character_limit: null,
            display_order: 0,
            source: "USER_INPUT",
            updated_at: now,
          },
        ],
      });
    }
    if (path === "/api/v1/companies" || path === "/api/v1/jobs") {
      return route.fulfill({ json: { items: [], next_cursor: null } });
    }
    if (path === "/api/v1/questions/question-e2e/selection" && request.method() === "GET") {
      return selection
        ? route.fulfill({ json: selection })
        : route.fulfill({ status: 404, json: notFound });
    }
    if (
      path === "/api/v1/questions/question-e2e/recommendation-runs" &&
      request.method() === "POST"
    ) {
      return route.fulfill({ status: 202, json: run });
    }
    if (path === "/api/v1/recommendation-runs/w4-run-e2e") {
      return route.fulfill({ json: run });
    }
    if (path === "/api/v1/recommendation-runs/w4-run-e2e/candidates") {
      return route.fulfill({ json: { items: [candidate], next_cursor: null } });
    }
    if (path === "/api/v1/recommendation-candidates/w4-candidate-1/select") {
      selection = {
        selection_id: "selection-w4-e2e",
        project_id: "project-e2e",
        question_id: "question-e2e",
        recommendation_run_id: "w4-run-e2e",
        candidate_id: "w4-candidate-1",
        episode_ref: { episode_id: "episode-1", version: 1 },
        selected_at: now,
        warnings: ["LIMITED_RESULT"],
      };
      return route.fulfill({ status: 201, json: selection });
    }
    return route.fulfill({ status: 404, json: notFound });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "지원 작업" }).click();
  await page.getByRole("button", { name: /W4 통합 검증/ }).click();
  await page.getByRole("button", { name: /QUESTION 01/ }).click();
  await page.getByRole("button", { name: "추천 분석 시작" }).click();
  await expect(page.getByText("실제 엔진 결과")).toBeVisible();
  await expect(page.getByText("제한된 결과")).toBeVisible();
  await expect(page.getByText("W4_SYNTHETIC_ACCEPTANCE_ONLY")).toBeVisible();
  await page.getByRole("button", { name: "소재로 선택" }).click();
  await expect(page.getByText("선택한 소재")).toBeVisible();
  expect(privateDestinations).toEqual([]);
});
