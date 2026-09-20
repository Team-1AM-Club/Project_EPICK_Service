import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RecommendationCandidatesView } from "@/components/recommendation-candidates";
import {
  makeRecommendationCandidate,
  makeRecommendationRun,
} from "../mocks/recommendation-scenarios";

describe("ENGINE recommendation projection", () => {
  it("keeps ENGINE origin separate from LIMITED quality and hides private transport", () => {
    const { container } = render(
      <RecommendationCandidatesView
        run={makeRecommendationRun({
          result_origin: "ENGINE",
          status: "LIMITED",
          result_status: "LIMITED",
          limited_analysis: true,
          limitations: ["W4_SYNTHETIC_ACCEPTANCE_ONLY"],
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

    expect(screen.getByText("실제 엔진 결과")).toHaveAttribute(
      "data-result-origin",
      "ENGINE",
    );
    expect(screen.getByText("제한된 결과")).toBeVisible();
    expect(screen.getByText("W4_SYNTHETIC_ACCEPTANCE_ONLY")).toBeVisible();
    expect(container.textContent).not.toContain("queue");
    expect(container.textContent).not.toContain("lease");
    expect(container.textContent).not.toContain("private");
  });
});
