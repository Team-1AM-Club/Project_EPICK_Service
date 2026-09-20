import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { JobPanelView } from "@/components/job-panel";
import { makeJob } from "../mocks/job-scenarios";

describe("JobPanelView", () => {
  it("shows progress and only server-returned required actions", async () => {
    const onAction = vi.fn();
    render(<JobPanelView job={makeJob({ status: "WAITING_USER", completeness: "partial", limitations: ["공식 공고 누락"] })} onAction={onAction} onRetry={vi.fn()} onCancel={vi.fn()} pending={false} />);
    expect(screen.getByText("사용자 선택 필요")).toBeVisible();
    expect(screen.getByText("공식 공고 누락")).toBeVisible();
    expect(screen.getByRole("button", { name: "확보한 자료로 계속" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "다시 시도" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "확보한 자료로 계속" }));
    expect(onAction).toHaveBeenCalledOnce();
  });

  it("renders retryable failure, rate-limit retry, cancel request, and final cancellation safely", () => {
    const { rerender } = render(<JobPanelView job={makeJob({ status: "FAILED_RETRYABLE", failure: { code: "UPSTREAM_TIMEOUT", message: "다시 시도할 수 있습니다.", retryable: true, retry_after_seconds: null } })} onAction={vi.fn()} onRetry={vi.fn()} onCancel={vi.fn()} pending={false} />);
    expect(screen.getByText("재시도 가능한 실패")).toBeVisible();
    expect(screen.getByText("다시 시도할 수 있습니다.")).toBeVisible();
    rerender(<JobPanelView job={makeJob({ status: "PAUSED_RATE_LIMIT", failure: { code: "RATE_LIMIT", message: null, retryable: true, retry_after_seconds: 30 } })} onAction={vi.fn()} onRetry={vi.fn()} onCancel={vi.fn()} pending={false} />);
    expect(screen.getByText("요청 한도로 일시 중지")).toBeVisible();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeDisabled();
    rerender(<JobPanelView job={makeJob({ status: "CANCEL_REQUESTED" })} onAction={vi.fn()} onRetry={vi.fn()} onCancel={vi.fn()} pending={false} />);
    expect(screen.getByText("중단 요청 처리 중")).toBeVisible();
    rerender(<JobPanelView job={makeJob({ status: "CANCELLED" })} onAction={vi.fn()} onRetry={vi.fn()} onCancel={vi.fn()} pending={false} />);
    expect(screen.getByText("작업 중단됨")).toBeVisible();
  });
});
