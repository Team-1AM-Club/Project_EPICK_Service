import { describe, expect, it } from "vitest";

import { presentJob } from "@/lib/jobs/presentation";
import { makeJob } from "../mocks/job-scenarios";

describe("Job presentation", () => {
  it.each([
    [makeJob({ status: "QUEUED", dispatch_status: "OUTBOX_PENDING" }), "요청 접수", true],
    [makeJob({ status: "QUEUED", dispatch_status: "ENQUEUED" }), "실행 대기", true],
    [makeJob({ status: "RUNNING", stage: "COLLECTING_SOURCES" }), "기업 자료 수집 중", true],
    [makeJob({ status: "RUNNING", stage: "PROJECTING_INDEX" }), "검색 반영 중", true],
    [makeJob({ status: "RUNNING", stage: "RETRIEVING_CANDIDATES" }), "소재 추천 중", true],
    [makeJob({ status: "WAITING_USER" }), "사용자 선택 필요", false],
    [makeJob({ status: "PAUSED_RATE_LIMIT" }), "요청 한도로 일시 중지", false],
    [makeJob({ status: "SUCCEEDED", completeness: "complete" }), "결과 확인 가능", false],
    [makeJob({ status: "FAILED_RETRYABLE" }), "재시도 가능한 실패", false],
    [makeJob({ status: "FAILED_FINAL" }), "처리하지 못함", false],
    [makeJob({ status: "CANCEL_REQUESTED" }), "중단 요청 처리 중", true],
    [makeJob({ status: "CANCELLED" }), "작업 중단됨", false],
  ] as const)("maps public axes without inventing a lifecycle enum", (job, title, polls) => {
    const result = presentJob(job);
    expect(result.title).toBe(title);
    expect(result.shouldPoll).toBe(polls);
  });

  it("lets dispatch blocking and invalidation override misleading lifecycle text", () => {
    expect(presentJob(makeJob({ status: "RUNNING", dispatch_status: "BLOCKED" })).title).toBe(
      "전달 차단 · 확인 필요",
    );
    const invalidated = presentJob(makeJob({ status: "RUNNING", dispatch_status: "INVALIDATED" }));
    expect(invalidated.title).toBe("요청 무효화");
    expect(invalidated.shouldPoll).toBe(false);
  });

  it("uses a safe non-success fallback for unknown stages", () => {
    const result = presentJob(makeJob({ status: "RUNNING", stage: "NEW_PRIVATE_STAGE" }));
    expect(result.title).toBe("분석 진행 중");
    expect(result.description).toContain("현재 상태를 확인");
  });
});
