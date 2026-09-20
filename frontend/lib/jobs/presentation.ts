import type { Job } from "@/lib/api/jobs";

export type JobPresentation = Readonly<{
  title: string;
  description: string;
  tone: "neutral" | "progress" | "warning" | "danger" | "success";
  shouldPoll: boolean;
  terminal: boolean;
  canCancel: boolean;
}>;

const COLLECTION_STAGES = new Set([
  "COLLECTING_SOURCES",
  "SOURCE_COLLECTION",
  "FETCHING_SOURCES",
  "COLLECTING",
]);
const PROJECTION_STAGES = new Set([
  "PROJECTING_INDEX",
  "PROJECTION",
  "GRAPH_PROJECTION",
  "VECTOR_INDEX",
  "INDEXING",
]);
const MATCHING_STAGES = new Set([
  "RETRIEVING_CANDIDATES",
  "MATCHING",
  "RECOMMENDING",
]);

export function presentJob(job: Job): JobPresentation {
  if (job.dispatch_status === "INVALIDATED") {
    return presentation("요청 무효화", "현재 요청은 더 이상 실행되지 않습니다.", "warning", false, true, false);
  }
  if (job.status === "WAITING_USER") {
    return presentation("사용자 선택 필요", "서버가 허용한 행동 중 하나를 선택해 주세요.", "warning", false, false, false);
  }
  if (job.status === "PAUSED_RATE_LIMIT") {
    return presentation("요청 한도로 일시 중지", retryAfter(job), "warning", false, false, false);
  }
  if (job.status === "SUCCEEDED") {
    const usable = job.completeness === "complete";
    return presentation(
      usable ? "결과 확인 가능" : "제한된 결과 확인 필요",
      usable ? "분석이 완료되었습니다." : "완전하지 않은 결과이므로 제한 사항을 확인해 주세요.",
      usable ? "success" : "warning",
      false,
      true,
      false,
    );
  }
  if (job.status === "FAILED_RETRYABLE") {
    return presentation("재시도 가능한 실패", job.failure.message ?? "서버가 허용할 때 다시 시도할 수 있습니다.", "danger", false, true, false);
  }
  if (job.status === "FAILED_FINAL") {
    return presentation("처리하지 못함", job.failure.message ?? "작업을 완료하지 못했습니다.", "danger", false, true, false);
  }
  if (job.status === "CANCELLED") {
    return presentation("작업 중단됨", "작업이 중단되었습니다.", "neutral", false, true, false);
  }
  if (job.status === "CANCEL_REQUESTED") {
    return presentation("중단 요청 처리 중", "실행 주체가 중단 요청을 반영하고 있습니다.", "progress", true, false, false);
  }
  if (job.dispatch_status === "BLOCKED") {
    return presentation("전달 차단 · 확인 필요", "실행 전달이 차단되어 현재 상태를 확인하고 있습니다.", "warning", true, false, false);
  }
  if (job.status === "QUEUED") {
    if (job.dispatch_status === "OUTBOX_PENDING") {
      return presentation("요청 접수", "요청이 안전하게 접수됐으며 아직 실행 전달 전입니다.", "neutral", true, false, true);
    }
    return presentation("실행 대기", "실행 요청이 전달됐으며 작업 시작을 기다리고 있습니다.", "progress", true, false, true);
  }
  if (job.status === "RUNNING") {
    if (job.stage && COLLECTION_STAGES.has(job.stage)) {
      return presentation("기업 자료 수집 중", "허용된 기업 자료를 확인하고 있습니다.", "progress", true, false, true);
    }
    if (job.stage && PROJECTION_STAGES.has(job.stage)) {
      return presentation("검색 반영 중", "확인된 자료를 검색 가능한 상태로 반영하고 있습니다.", "progress", true, false, true);
    }
    if (job.stage && MATCHING_STAGES.has(job.stage)) {
      return presentation("소재 추천 중", "문항과 연결할 경험을 확인하고 있습니다.", "progress", true, false, true);
    }
    return presentation("분석 진행 중", "알 수 없는 내부 단계를 성공으로 간주하지 않고 현재 상태를 확인합니다.", "progress", true, false, true);
  }
  return presentation("현재 상태 확인 필요", "서버의 현재 상태를 다시 확인해 주세요.", "warning", false, false, false);
}

function retryAfter(job: Job) {
  const seconds = job.failure.retry_after_seconds;
  return seconds === null ? "서버가 허용한 명시적 행동을 선택해 주세요." : `${seconds}초 후 서버가 허용한 명시적 재시도를 선택할 수 있습니다.`;
}

function presentation(
  title: string,
  description: string,
  tone: JobPresentation["tone"],
  shouldPoll: boolean,
  terminal: boolean,
  canCancel: boolean,
): JobPresentation {
  return { title, description, tone, shouldPoll, terminal, canCancel };
}
