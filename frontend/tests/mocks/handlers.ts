import { http, HttpResponse } from "msw";

const apiOrigin = "http://localhost:8000";

export const publicApiHandlers = [
  http.get(`${apiOrigin}/api/v1/users/me`, () =>
    HttpResponse.json({
      id: "00000000-0000-4000-8000-000000000001",
      display_name: "합성 사용자",
      email: "synthetic@example.test",
      account_status: "ACTIVE",
      created_at: "2026-09-20T00:00:00Z",
    }),
  ),
  http.get(`${apiOrigin}/api/v1/home`, () =>
    HttpResponse.json({
      resume_items: [],
      projects: { in_progress_count: 0, needs_review_count: 0, recent: [] },
      experience_store: { activity_count: 0, draft_count: 0 },
      notifications: { unread_count: 0, critical_count: 0 },
      running_job_count: 0,
    }),
  ),
  http.get(`${apiOrigin}/api/v1/resume-items`, () =>
    HttpResponse.json({ items: [], next_cursor: null }),
  ),
  http.get(`${apiOrigin}/api/v1/jobs`, () =>
    HttpResponse.json({ items: [], next_cursor: null }),
  ),
];
