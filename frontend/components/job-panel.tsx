"use client";

import { Loader2, RotateCcw, Square, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import type { Job, JobRequiredAction } from "@/lib/api/jobs";
import { presentJob } from "@/lib/jobs/presentation";
import { useJobActions } from "@/lib/jobs/use-job-actions";
import { useJobPolling } from "@/lib/jobs/use-job-polling";

export function JobPanel({ jobId }: { jobId: string }) {
  const job = useJobPolling(jobId);
  const actions = useJobActions(jobId);
  if (job.isLoading) return <div className="job-panel" role="status">분석 상태를 불러오고 있습니다.</div>;
  if (job.error || !job.data) {
    return <div className="job-panel" role="alert"><TriangleAlert /><h3>분석 상태를 확인하지 못했습니다.</h3><p>네트워크 연결 후 현재 상태를 다시 확인합니다. 작업을 자동으로 재실행하지 않습니다.</p></div>;
  }
  const reportError = (error: unknown) => toast.error(error instanceof Error ? error.message : "Job 행동을 처리하지 못했습니다.");
  return <JobPanelView
    job={job.data}
    pending={actions.pending}
    onAction={(action) => void actions.submit(action).catch(reportError)}
    onRetry={(action) => void actions.retry(action, job.data.checkpoint).catch(reportError)}
    onCancel={() => void actions.cancel().catch(reportError)}
  />;
}

export function JobPanelView({ job, pending, onAction, onRetry, onCancel }: {
  job: Job;
  pending: boolean;
  onAction: (action: JobRequiredAction) => void;
  onRetry: (action: JobRequiredAction) => void;
  onCancel: () => void;
}) {
  const view = presentJob(job);
  const progress = job.progress.percent;
  return <section className={`job-panel job-${view.tone}`} aria-live="polite">
    <div className="job-icon">{view.shouldPoll ? <Loader2 className="animate-spin" /> : view.tone === "warning" || view.tone === "danger" ? <TriangleAlert /> : null}</div>
    <h3>{view.title}</h3>
    <p>{view.description}</p>
    {progress !== null && <div aria-label={`진행률 ${progress}%`}><progress max={100} value={progress} /><span>{progress}%</span></div>}
    {job.failure.message && job.failure.message !== view.description && <p role="alert">{job.failure.message}</p>}
    {(job.limitations?.length ?? 0) > 0 && <div className="notice amber"><strong>제한 사항</strong><ul>{job.limitations?.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    <div className="inline-actions">
      {job.required_actions?.map((action) => action.code === "RETRY" ? (
        <button key={action.id} className="primary" disabled={pending || (job.status === "PAUSED_RATE_LIMIT" && (job.failure.retry_after_seconds ?? 0) > 0)} onClick={() => job.checkpoint.available ? onRetry(action) : onAction(action)}><RotateCcw size={15} /> 다시 시도</button>
      ) : (
        <button key={action.id} className={action.code === "STOP" ? "outline-button" : "primary"} disabled={pending} onClick={() => onAction(action)}>
          {action.code === "CONTINUE_LIMITED" ? "확보한 자료로 계속" : "작업 중단"}
        </button>
      ))}
      {view.canCancel && <button className="text-button" disabled={pending} onClick={onCancel}><Square size={14} /> 중단 요청</button>}
    </div>
  </section>;
}
