"use client";

import { useMemo, useState } from "react";
import { Check, CircleAlert, Plus } from "lucide-react";
import { toast } from "sonner";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { createIdempotentIntent } from "@/lib/api/idempotency";
import {
  describeRecommendationError,
  type RecommendationCandidate,
  type RecommendationRun,
} from "@/lib/api/recommendations";
import type { MaterialSelection } from "@/lib/api/selections";
import {
  candidateAvailability,
  recommendationRunState,
} from "@/lib/recommendations/availability";
import {
  useCreateRecommendationRun,
  useRecommendationCandidates,
  useRecommendationRun,
} from "@/lib/queries/recommendations";
import { useMaterialSelection } from "@/lib/queries/selections";
import { useMaterialSelectionActions } from "@/lib/selections/use-material-selection";
import { ResultOriginBadge } from "./result-origin-badge";

type QuestionSummary = Readonly<{
  id: string;
  current_version: number;
  prompt: string;
}>;

export function RecommendationWorkspace({
  question,
  onAddExperience,
}: {
  question: QuestionSummary;
  onAddExperience: () => void;
}) {
  const [acceptedRun, setAcceptedRun] = useState<{
    questionId: string;
    runId: string;
  } | null>(null);
  const selection = useMaterialSelection(question.id);
  const acceptedRunId = acceptedRun?.questionId === question.id ? acceptedRun.runId : null;
  const runId = acceptedRunId ?? selection.data?.recommendation_run_id ?? null;
  const run = useRecommendationRun(runId);
  const candidates = useRecommendationCandidates(runId);
  const createRun = useCreateRecommendationRun();
  const actions = useMaterialSelectionActions(run.data ?? null, selection.data ?? null);

  const start = async () => {
    try {
      const accepted = await createRun.mutateAsync({
        questionId: question.id,
        body: {
          question_version: question.current_version,
          snapshot_version: 0,
          candidate_limit: 5,
          include_excluded: false,
          allow_limited_analysis: true,
        },
        key: createIdempotentIntent({
          questionId: question.id,
          questionVersion: question.current_version,
        }).key,
      });
      setAcceptedRun({ questionId: question.id, runId: accepted.id });
    } catch (error) {
      toast.error(describeRecommendationError(error));
    }
  };

  if (selection.isLoading) {
    return <div className="job-panel" role="status">저장된 소재 선택을 확인하고 있습니다.</div>;
  }
  if (selection.error) {
    return (
      <div className="job-panel" role="alert">
        <CircleAlert />
        <h3>저장된 소재 선택을 확인하지 못했습니다.</h3>
        <p>{describeRecommendationError(selection.error)}</p>
      </div>
    );
  }
  if (!runId) {
    return (
      <div className="job-panel">
        <h3>{question.prompt}</h3>
        <p>현재 문항 버전과 경험 스냅샷을 고정한 뒤 추천 후보를 준비합니다.</p>
        <div className="inline-actions">
          <button className="primary" disabled={createRun.isPending} onClick={() => void start()}>
            추천 분석 시작
          </button>
          <button className="outline-button" onClick={onAddExperience}>
            경험 추가하기
          </button>
        </div>
      </div>
    );
  }
  if (run.isLoading || candidates.isLoading) {
    return <div className="job-panel" role="status">추천 결과를 불러오고 있습니다.</div>;
  }
  if (run.error || candidates.error || !run.data) {
    return (
      <div className="job-panel" role="alert">
        <CircleAlert />
        <h3>추천 결과를 불러오지 못했습니다.</h3>
        <p>{describeRecommendationError(run.error ?? candidates.error)}</p>
      </div>
    );
  }
  return (
    <RecommendationCandidatesView
      run={run.data}
      candidates={candidates.data?.items ?? []}
      selection={selection.data ?? null}
      questionVersion={question.current_version}
      pending={actions.pending}
      onSelect={(candidate) => void actions.select(candidate).catch((error) => {
        toast.error(describeRecommendationError(error));
      })}
      onClear={() => void actions.clear().catch((error) => {
        toast.error(describeRecommendationError(error));
      })}
      onAddExperience={onAddExperience}
    />
  );
}

export function RecommendationCandidatesView({
  run,
  candidates,
  selection,
  questionVersion,
  pending,
  onSelect,
  onClear,
  onAddExperience,
}: {
  run: RecommendationRun;
  candidates: RecommendationCandidate[];
  selection: MaterialSelection | null;
  questionVersion: number;
  pending: boolean;
  onSelect: (candidate: RecommendationCandidate) => void;
  onClear: () => void;
  onAddExperience: () => void;
}) {
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const state = recommendationRunState(run, questionVersion);
  const compared = useMemo(
    () => candidates.filter((candidate) => compareIds.includes(candidate.id)),
    [candidates, compareIds],
  );

  if (state === "pending") {
    return <div className="job-panel" role="status">추천 소재를 준비하고 있습니다.</div>;
  }
  if (state === "failed") {
    return (
      <div className="job-panel" role="alert">
        <CircleAlert />
        <h3>추천 결과를 준비하지 못했습니다.</h3>
        <p>시스템 실패와 정상적인 후보 없음은 구분됩니다. 현재 결과는 선택할 수 없습니다.</p>
      </div>
    );
  }

  return (
    <section aria-label="추천 소재 후보">
      <div className="section-heading candidate-heading">
        <div>
          <h2>추천 소재 후보</h2>
          <p>근거·강점·한계를 비교한 뒤 문항별 소재를 저장하세요.</p>
        </div>
        <div className="inline-actions">
          <ResultOriginBadge origin={run.result_origin} />
          {state === "limited" && <span className="tag amber">제한된 결과</span>}
        </div>
      </div>
      {state === "stale" && (
        <div className="notice amber" role="alert">
          <CircleAlert size={18} />
          <p>문항이 변경되어 이전 추천을 선택할 수 없습니다.</p>
        </div>
      )}
      {(run.limitations ?? []).map((limitation) => (
        <div className="notice amber" key={limitation}>{limitation}</div>
      ))}
      {candidates.length === 0 ? (
        <div className="empty-state">
          <h2>이 문항에 연결되는 소재를 찾지 못했어요.</h2>
          <p>시스템 오류가 아닙니다. 경험을 추가한 뒤 새 분석을 요청할 수 있습니다.</p>
          <button className="outline-button" onClick={onAddExperience}>
            <Plus size={16} /> 경험 추가하기
          </button>
        </div>
      ) : (
        candidates.map((candidate) => {
          const availability = candidateAvailability(run, candidate, questionVersion);
          const selected = selection?.candidate_id === candidate.id;
          const checked = compareIds.includes(candidate.id);
          return (
            <article className={`candidate-card ${selected ? "selected" : ""}`} key={candidate.id}>
              <label className="compare-check">
                <input
                  type="checkbox"
                  aria-label={`${candidate.short_reason} 비교`}
                  checked={checked}
                  onChange={(event) => setCompareIds((current) =>
                    event.target.checked
                      ? [...current, candidate.id]
                      : current.filter((id) => id !== candidate.id),
                  )}
                />
                비교하기
              </label>
              <h3>{candidate.short_reason}</h3>
              <p className="candidate-meta">
                {matchStatusLabel(candidate.match_status)} · 결과 {candidate.result_version}
              </p>
              {candidate.strength_summary && (
                <div className="candidate-reason">
                  <strong>강점</strong>
                  <p>{candidate.strength_summary}</p>
                </div>
              )}
              {(candidate.limitation_summary || availability.limited) && (
                <div className="candidate-limit">
                  <CircleAlert size={16} />
                  {candidate.limitation_summary ?? "제한된 검증 결과이므로 내용을 확인해 주세요."}
                </div>
              )}
              {availability.reason && !availability.stale && (
                <p className="candidate-limit">{availability.reason}</p>
              )}
              <div className="candidate-actions">
                <span>{selected ? "현재 선택된 소재" : "문항 소재로 저장"}</span>
                <button
                  className={selected ? "selected-button" : "primary"}
                  disabled={pending || selected || !availability.selectable}
                  onClick={() => onSelect(candidate)}
                >
                  {selected ? <><Check size={16} /> 선택됨</> : "소재로 선택"}
                </button>
              </div>
            </article>
          );
        })
      )}
      {compareIds.length > 1 && (
        <button className="outline-button" onClick={() => setCompareOpen(true)}>
          선택한 {compareIds.length}개 비교
        </button>
      )}
      {selection && (
        <div className="saved-selection">
          <span><Check size={17} /> 선택한 소재</span>
          <button className="text-button" disabled={pending} onClick={onClear}>선택 해제</button>
        </div>
      )}
      <Dialog open={compareOpen} onOpenChange={setCompareOpen}>
        <DialogContent className="compare-dialog">
          <DialogHeader><DialogTitle>추천 소재 비교</DialogTitle></DialogHeader>
          <div className="comparison-grid">
            {compared.map((candidate) => (
              <div className="compare-column" key={candidate.id}>
                <span className="tag">후보 {candidate.candidate_no}</span>
                <h3>{candidate.short_reason}</h3>
                <h4>강점</h4><p>{candidate.strength_summary ?? "제공된 강점 요약 없음"}</p>
                <h4>한계</h4><p>{candidate.limitation_summary ?? "표시할 한계 없음"}</p>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function matchStatusLabel(status: RecommendationCandidate["match_status"]) {
  switch (status) {
    case "DIRECT_MATCH": return "직접 연결";
    case "PARTIAL_RELEVANCE": return "부분 연결";
    case "NEEDS_VERIFICATION": return "확인 필요";
    case "NO_RELEVANT_EVIDENCE": return "직접 근거 없음";
  }
}
