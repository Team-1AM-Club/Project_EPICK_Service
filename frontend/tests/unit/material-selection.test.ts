import { describe, expect, it, vi } from "vitest";

import {
  createMaterialSelectionExecutor,
  createSelectionResponseGuard,
  selectionInvalidationTargets,
} from "@/lib/selections/use-material-selection";
import {
  makeMaterialSelection,
  makeRecommendationCandidate,
  makeRecommendationRun,
} from "../mocks/recommendation-scenarios";

describe("material selection", () => {
  it("deduplicates select, forwards the result version, and supports replace and clear", async () => {
    let resolve!: (value: ReturnType<typeof makeMaterialSelection>) => void;
    const select = vi.fn().mockReturnValue(new Promise((next) => { resolve = next; }));
    const clear = vi.fn().mockResolvedValue(undefined);
    const executor = createMaterialSelectionExecutor(
      { select, clear },
      () => ({ key: "selection-intent", requestFingerprint: "fingerprint" }),
    );
    const run = makeRecommendationRun();
    const candidate = makeRecommendationCandidate();

    const first = executor.select(run, candidate, false);
    const duplicate = executor.select(run, candidate, false);
    expect(select).toHaveBeenCalledOnce();
    expect(select).toHaveBeenCalledWith(
      candidate.id,
      {
        question_id: run.question_id,
        recommendation_run_id: run.id,
        result_version: candidate.result_version,
        replace_existing: false,
      },
      "selection-intent",
    );
    resolve(makeMaterialSelection());
    await Promise.all([first, duplicate]);

    await executor.select(run, candidate, true);
    expect(select.mock.calls[1][1].replace_existing).toBe(true);
    await executor.clear(run.question_id);
    expect(clear).toHaveBeenCalledWith(run.question_id, "selection-intent");
  });

  it("returns only targeted selection, question, and project invalidation keys", () => {
    expect(selectionInvalidationTargets("question-1", "project-1")).toEqual([
      expect.arrayContaining(["selections", "question-1"]),
      expect.arrayContaining(["questions", "detail", "question-1"]),
      expect.arrayContaining(["projects", "detail", "project-1"]),
      expect.arrayContaining(["projects", "list"]),
    ]);
  });

  it("rejects an older intent and any response from an older client generation", () => {
    let generation = 3;
    const guard = createSelectionResponseGuard(() => generation);
    const older = guard.begin("question-1");
    const newest = guard.begin("question-1");
    expect(guard.isCurrent(older)).toBe(false);
    expect(guard.isCurrent(newest)).toBe(true);
    generation += 1;
    expect(guard.isCurrent(newest)).toBe(false);
  });
});
