"use client";

import { useRef, useState } from "react";
import { ArrowUpRight, FileText, Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  createIdempotentIntent,
  reuseIntent,
  type IdempotentIntent,
} from "@/lib/api/idempotency";
import { companyInitial, companyStatusText } from "@/lib/company-data";
import {
  useLatestSourceCollection,
  useStartSourceCollection,
} from "@/lib/jobs/source-collection";
import { useJobPolling } from "@/lib/jobs/use-job-polling";
import { useCompanies, useProjects } from "@/lib/queries/projects";

export function CompanyExplorer({
  onPrepare,
}: {
  onPrepare: (company: string, role: string) => void;
}) {
  const companies = useCompanies();
  const projects = useProjects({ limit: 100 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [collecting, setCollecting] = useState(false);
  const [officialUrl, setOfficialUrl] = useState("");
  const [purpose, setPurpose] = useState<"COMPANY_PROFILE" | "JOB_POSTING">("COMPANY_PROFILE");
  const [acceptedJobId, setAcceptedJobId] = useState<string | null>(null);
  const collectionIntent = useRef<IdempotentIntent | null>(null);
  const officialUrlCompanyId = useRef<string | null>(null);

  const effectiveSelectedId = selectedId ?? companies.data?.items?.[0]?.id ?? null;
  const company = companies.data?.items?.find((item) => item.id === effectiveSelectedId) ?? null;
  const project = projects.data?.items?.find((item) => item.company_id === effectiveSelectedId) ?? null;
  const latestCollection = useLatestSourceCollection(project?.id ?? null);
  const durableJobId = acceptedJobId ?? latestCollection.data?.job_id ?? null;
  const job = useJobPolling(durableJobId);
  const startCollection = useStartSourceCollection(project?.id ?? null);

  const openCollection = () => {
    if (officialUrlCompanyId.current !== company?.id) {
      setOfficialUrl(company?.official_domain ? `https://${company.official_domain}/` : "");
      officialUrlCompanyId.current = company?.id ?? null;
    }
    setCollecting(true);
  };

  const submitCollection = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!project) {
      toast.error("이 기업의 지원 프로젝트를 먼저 만들어 주세요.");
      return;
    }
    const body = {
      source_type: "OFFICIAL_URL" as const,
      official_url: officialUrl.trim(),
      purpose,
    };
    const intentPayload = { projectId: project.id, ...body };
    let key: string;
    try {
      key = collectionIntent.current
        ? reuseIntent(collectionIntent.current, intentPayload)
        : (collectionIntent.current = createIdempotentIntent(intentPayload)).key;
    } catch {
      collectionIntent.current = createIdempotentIntent(intentPayload);
      key = collectionIntent.current.key;
    }
    try {
      const accepted = await startCollection.mutateAsync({
        body,
        key,
      });
      setAcceptedJobId(accepted.job_id);
      setCollecting(false);
      toast.success(
        accepted.replayed
          ? "기존 수집 작업을 이어서 표시합니다."
          : "수집 작업을 접수했습니다.",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "수집 작업을 접수하지 못했습니다.");
    }
  };

  if (companies.isLoading) {
    return <div className="company-explorer" role="status">기업 목록을 불러오고 있습니다.</div>;
  }
  if (companies.error) {
    return <div className="company-explorer" role="alert">기업 목록을 불러오지 못했습니다.</div>;
  }

  return (
    <div className="company-explorer">
      <aside className="company-sidebar" aria-label="기업 목록">
        <div className="epick-brand">
          <img src="/figma/epick.svg" alt="EPICK" width="104" height="27" />
        </div>
        <div className="company-picker">
          {companies.data?.items?.map((item) => (
            <button
              key={item.id}
              className={`company-option ${item.id === effectiveSelectedId ? "selected" : ""}`}
              aria-pressed={item.id === effectiveSelectedId}
              onClick={() => setSelectedId(item.id)}
            >
              <span className="figma-company-logo custom">{companyInitial(item)}</span>
              <span>
                <strong>{item.display_name}</strong>
                <small>{companyStatusText(item)}</small>
              </span>
            </button>
          ))}
        </div>
      </aside>

      <div className="company-main">
        <div className="company-banner empty-banner" />
        <div className="company-content">
          {company ? (
            <>
              <section className="company-profile glass-panel" aria-label="기업 소개">
                <div className="company-profile-copy">
                  <span className="figma-company-logo custom">{companyInitial(company)}</span>
                  <p>{company.official_domain ?? "공식 도메인 미등록"}</p>
                  <h1>{company.display_name}</h1>
                </div>
              </section>
              <div className="update-row">
                <span>
                  {job.data
                    ? `수집 상태: ${job.data.status}${job.data.stage ? ` · ${job.data.stage}` : ""}`
                    : "아직 시작한 수집 작업이 없어요"}
                </span>
                <button
                  onClick={openCollection}
                  disabled={!project || startCollection.isPending}
                >
                  {startCollection.isPending ? (
                    <><Loader2 size={12} className="animate-spin" /> 접수 중</>
                  ) : "공식 URL 수집"}
                </button>
              </div>
              <div className="glass-panel company-empty">
                <FileText size={26} />
                <h2>{project ? "공식 자료를 수집할 수 있어요" : "지원 프로젝트를 먼저 만들어 주세요"}</h2>
                <p>
                  {project
                    ? "등록한 공식 URL은 W1 Job으로 저장되며 새로고침 후에도 진행 상태를 확인할 수 있습니다."
                    : `${company.display_name}의 지원 프로젝트를 만들면 실제 수집을 시작할 수 있어요.`}
                </p>
                {!project && (
                  <button className="primary" onClick={() => onPrepare(company.display_name, "")}>
                    이 기업으로 지원 준비하기 <ArrowUpRight size={16} />
                  </button>
                )}
              </div>
            </>
          ) : (
            <div className="glass-panel company-empty">
              <FileText size={26} />
              <h2>식별 완료된 기업이 없어요</h2>
              <p>W1 기업 카탈로그에 검증된 기업이 등록되면 이곳에 표시됩니다.</p>
            </div>
          )}
        </div>
      </div>

      <Dialog open={collecting} onOpenChange={setCollecting}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>공식 URL 수집 시작</DialogTitle>
            <DialogDescription>
              선택한 기업의 공식 도메인과 일치하는 HTTP(S) 주소만 접수됩니다.
            </DialogDescription>
          </DialogHeader>
          <form className="experience-form" onSubmit={submitCollection}>
            <label>
              공식 URL
              <input
                required
                type="url"
                maxLength={2048}
                value={officialUrl}
                onChange={(event) => setOfficialUrl(event.target.value)}
              />
            </label>
            <label>
              자료 종류
              <select
                value={purpose}
                onChange={(event) => setPurpose(event.target.value as typeof purpose)}
              >
                <option value="COMPANY_PROFILE">기업 공식 자료</option>
                <option value="JOB_POSTING">채용 공고</option>
              </select>
            </label>
            <button className="primary" type="submit" disabled={startCollection.isPending}>
              수집 시작
            </button>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
