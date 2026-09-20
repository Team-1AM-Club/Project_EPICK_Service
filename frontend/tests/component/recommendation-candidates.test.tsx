import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RecommendationCandidatesView } from "@/components/recommendation-candidates";
import {
  makeMaterialSelection,
  makeRecommendationCandidate,
  makeRecommendationRun,
} from "../mocks/recommendation-scenarios";

const candidate = makeRecommendationCandidate();

describe("RecommendationCandidatesView", () => {
  it("shows usable READY engine candidates and the recovered selection", () => {
    render(
      <RecommendationCandidatesView
        run={makeRecommendationRun()}
        candidates={[candidate]}
        selection={makeMaterialSelection()}
        questionVersion={2}
        pending={false}
        onSelect={vi.fn()}
        onClear={vi.fn()}
        onAddExperience={vi.fn()}
      />,
    );
    expect(screen.getByText("실제 엔진 결과")).toBeVisible();
    expect(screen.getByText(candidate.short_reason)).toBeVisible();
    expect(screen.getByRole("button", { name: "선택 해제" })).toBeVisible();
  });

  it("labels LIMITED synthetic results and keeps allowed candidates selectable", () => {
    render(
      <RecommendationCandidatesView
        run={makeRecommendationRun({
          status: "LIMITED",
          result_status: "LIMITED",
          result_origin: "SYNTHETIC",
          limited_analysis: true,
          limitations: ["공식 공고 누락"],
        })}
        candidates={[makeRecommendationCandidate({ validation_status: "LIMITED" })]}
        selection={null}
        questionVersion={2}
        pending={false}
        onSelect={vi.fn()}
        onClear={vi.fn()}
        onAddExperience={vi.fn()}
      />,
    );
    expect(screen.getByText("합성 검증 결과")).toBeVisible();
    expect(screen.getByText("제한된 결과")).toBeVisible();
    expect(screen.getByText("공식 공고 누락")).toBeVisible();
    expect(screen.getByRole("button", { name: "소재로 선택" })).toBeEnabled();
  });

  it("blocks stale output and distinguishes empty and failed results", () => {
    const { rerender } = render(
      <RecommendationCandidatesView
        run={makeRecommendationRun({ question_version: 1 })}
        candidates={[candidate]}
        selection={null}
        questionVersion={2}
        pending={false}
        onSelect={vi.fn()}
        onClear={vi.fn()}
        onAddExperience={vi.fn()}
      />,
    );
    expect(screen.getByText("문항이 변경되어 이전 추천을 선택할 수 없습니다.")).toBeVisible();
    expect(screen.getByRole("button", { name: "소재로 선택" })).toBeDisabled();

    rerender(
      <RecommendationCandidatesView
        run={makeRecommendationRun()}
        candidates={[]}
        selection={null}
        questionVersion={2}
        pending={false}
        onSelect={vi.fn()}
        onClear={vi.fn()}
        onAddExperience={vi.fn()}
      />,
    );
    expect(screen.getByText("이 문항에 연결되는 소재를 찾지 못했어요.")).toBeVisible();

    rerender(
      <RecommendationCandidatesView
        run={makeRecommendationRun({ status: "FAILED", result_status: "FAILED" })}
        candidates={[]}
        selection={null}
        questionVersion={2}
        pending={false}
        onSelect={vi.fn()}
        onClear={vi.fn()}
        onAddExperience={vi.fn()}
      />,
    );
    expect(screen.getByText("추천 결과를 준비하지 못했습니다.")).toBeVisible();
  });
});
