"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, ArrowUpRight, Check, Clock3, Layers, Plus, Search } from "lucide-react";
import { toast } from "sonner";

import { ApiConflictDialog } from "@/components/api-conflict-dialog";
import { JobPanel } from "@/components/job-panel";
import { RecommendationWorkspace } from "@/components/recommendation-candidates";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { createActivitiesApi, type ActivityListItem } from "@/lib/api/activities";
import { classifyConflict, type ConflictDescription } from "@/lib/api/conflicts";
import { createEpisodesApi } from "@/lib/api/episodes";
import {
  emptyExperienceForm,
  ExperienceSaveError,
  fromExperience,
  persistExperience,
  type ExperienceFormValue,
} from "@/lib/api/experience-adapter";
import { createIdempotentIntent } from "@/lib/api/idempotency";
import { createProjectsApi } from "@/lib/api/projects";
import { createQuestionsApi } from "@/lib/api/questions";
import { activityQueryKeys, useActivities, useActivity } from "@/lib/queries/activities";
import { episodeQueryKeys, useEpisode, useEpisodes } from "@/lib/queries/episodes";
import { projectQueryKeys, useCompanies, useProject, useProjects } from "@/lib/queries/projects";
import { questionQueryKeys, useQuestions } from "@/lib/queries/questions";
import { personalQueryKeys } from "@/lib/queries/query-client";
import { useJobs } from "@/lib/queries/jobs";

export function Heading({ eyebrow, title, description, children }: {
  eyebrow: string; title: string; description?: string; children?: ReactNode;
}) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}<span className="blue">.</span></h1>{description && <p>{description}</p>}</div>{children}</div>;
}

function ExperienceCard({ experience, onClick }: { experience: ActivityListItem; onClick: () => void }) {
  const draft = experience.registration_status === "DRAFT";
  return <button className="experience-card" onClick={onClick}>
    <div className="card-top"><span className="experience-icon blue"><Layers size={22} /></span><ArrowUpRight size={19} /></div>
    <span className="kicker">{experience.activity_type ?? "활동"}</span>
    <h3>{experience.title}</h3><p>{experience.organization_display ?? "소속을 입력하지 않았어요."}</p>
    <div className="card-footer"><span>{draft ? <Clock3 size={13} /> : <Check size={13} />} {draft ? "임시 저장" : "등록 완료"}</span><span>{experience.period_display ?? "기간 미입력"}</span></div>
  </button>;
}

export function Experiences({ onReturn, resumeActivityId }: { onReturn?: () => void; resumeActivityId?: string }) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<"all" | "DRAFT" | "COMPLETED">("all");
  const [search, setSearch] = useState("");
  const [editingId, setEditingId] = useState<string | null>(resumeActivityId ?? null);
  const [recoveryActivity, setRecoveryActivity] = useState<{ id: string; version: number } | null>(null);
  const [form, setForm] = useState<ExperienceFormValue>(emptyExperienceForm);
  const [conflict, setConflict] = useState<ConflictDescription | null>(null);
  const activities = useActivities({ q: search || undefined, status: filter === "all" ? undefined : filter });
  const activity = useActivity(editingId && editingId !== "new" ? editingId : null);
  const episodes = useEpisodes(editingId && editingId !== "new" ? editingId : null);
  const firstEpisodeId = episodes.data?.[0]?.id ?? null;
  const episode = useEpisode(firstEpisodeId);

  useEffect(() => { if (resumeActivityId) setEditingId(resumeActivityId); }, [resumeActivityId]);
  useEffect(() => {
    if (activity.data && (!firstEpisodeId || episode.data)) setForm(fromExperience(activity.data, episode.data));
  }, [activity.data, episode.data, firstEpisodeId]);

  const save = useMutation({
    mutationFn: async (mode: "draft" | "complete") => {
      if (!form.title.trim()) throw new Error("활동명을 입력해 주세요.");
      return persistExperience({
        form, mode,
        existing: activity.data || recoveryActivity ? {
          activityId: activity.data?.id ?? recoveryActivity!.id,
          activityVersion: activity.data?.current_version ?? recoveryActivity!.version,
          episodeId: episode.data?.id,
          episodeVersion: episode.data?.current_version,
        } : undefined,
        activities: createActivitiesApi(), episodes: createEpisodesApi(),
        makeKey: () => createIdempotentIntent({ operation: crypto.randomUUID() }).key,
      });
    },
    onSuccess: async () => {
      setEditingId(null); setRecoveryActivity(null); setForm(emptyExperienceForm);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: activityQueryKeys.all() }),
        queryClient.invalidateQueries({ queryKey: episodeQueryKeys.all() }),
        queryClient.invalidateQueries({ queryKey: personalQueryKeys.home() }),
        queryClient.invalidateQueries({ queryKey: personalQueryKeys.resumeItems() }),
      ]);
      toast.success("경험을 서버에 저장했어요.");
    },
    onError: (error) => {
      const next = classifyConflict(error);
      if (next) setConflict(next);
      else if (error instanceof ExperienceSaveError) {
        setRecoveryActivity({ id: error.activityId, version: error.activityVersion });
        toast.error(`${error.message} 내 경험에서 이어서 작성할 수 있습니다.`);
        void queryClient.invalidateQueries({ queryKey: activityQueryKeys.all() });
      } else toast.error(error instanceof Error ? error.message : "경험을 저장하지 못했습니다.");
    },
  });
  const openNew = () => { setRecoveryActivity(null); setForm(emptyExperienceForm); setEditingId("new"); };
  const close = () => { setEditingId(null); setRecoveryActivity(null); setForm(emptyExperienceForm); };
  const reload = async () => { setConflict(null); await Promise.all([activity.refetch(), episode.refetch()]); };

  return <>
    <Heading eyebrow="YOUR EXPERIENCE LIBRARY" title="내 경험"><button className="primary" onClick={openNew}><Plus size={18} /> 새 경험 기록</button></Heading>
    {onReturn && <button className="return-banner" onClick={onReturn}><ArrowLeft size={16} /> 원래 지원 작업으로 돌아가기</button>}
    <div className="toolbar">
      <Tabs value={filter} onValueChange={(value) => setFilter(value as typeof filter)}><TabsList variant="line"><TabsTrigger value="all">전체</TabsTrigger><TabsTrigger value="COMPLETED">등록 완료</TabsTrigger><TabsTrigger value="DRAFT">임시 저장</TabsTrigger></TabsList></Tabs>
      <label className="search-field"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="경험 검색" aria-label="경험 검색" /></label>
    </div>
    {activities.isLoading && <div role="status" className="empty-state">경험을 불러오고 있습니다.</div>}
    {activities.error && <div role="alert" className="empty-state">경험을 불러오지 못했습니다.</div>}
    {(activities.data?.items?.length ?? 0) > 0 ? <div className="experience-grid library-grid">{activities.data?.items?.map((item) => <ExperienceCard key={item.id} experience={item} onClick={() => setEditingId(item.id)} />)}</div> : !activities.isLoading && <div className="empty-state"><Search size={32} /><h2>{search ? "검색한 경험이 없어요" : "아직 기록된 경험이 없어요"}</h2><button className="outline-button" onClick={openNew}>경험 기록하기</button></div>}
    <Dialog open={editingId !== null} onOpenChange={(open) => !open && close()}><DialogContent className="large-dialog">
      <DialogHeader><DialogTitle>{editingId === "new" ? "새로운 경험 기록하기" : "경험 이어서 작성하기"}</DialogTitle><DialogDescription>입력하지 않은 값은 추측하지 않고 미제공 상태로 저장합니다.</DialogDescription></DialogHeader>
      {activity.isLoading && editingId !== "new" ? <p role="status">경험을 불러오고 있습니다.</p> : <ExperienceForm form={form} onChange={setForm} onSubmit={() => save.mutate("complete")} onDraft={() => save.mutate("draft")} pending={save.isPending} />}
    </DialogContent></Dialog>
    <ApiConflictDialog conflict={conflict} onClose={() => setConflict(null)} onReload={reload} />
  </>;
}

function ExperienceForm({ form, onChange, onSubmit, onDraft, pending }: {
  form: ExperienceFormValue; onChange: (value: ExperienceFormValue) => void; onSubmit: () => void; onDraft: () => void; pending: boolean;
}) {
  const set = (field: keyof ExperienceFormValue, value: string) => onChange({ ...form, [field]: value });
  return <form className="experience-form" onSubmit={(event) => { event.preventDefault(); onSubmit(); }}>
    <div className="form-grid">
      <label>활동명<input required value={form.title} onChange={(event) => set("title", event.target.value)} /></label>
      <label>소속 · 주관기관<input required value={form.organization} onChange={(event) => set("organization", event.target.value)} /></label>
      <label>시작일<input required type="date" value={form.startDate} onChange={(event) => set("startDate", event.target.value)} /></label>
      <label>종료일<input type="date" value={form.endDate} onChange={(event) => set("endDate", event.target.value)} /></label>
      <label>내 역할<input required value={form.role} onChange={(event) => set("role", event.target.value)} /></label>
      <label>결과 또는 현재 상태<input value={form.outcomeSummary} onChange={(event) => set("outcomeSummary", event.target.value)} /></label>
    </div>
    <label>기억에 남는 사건<textarea rows={5} value={form.story} onChange={(event) => set("story", event.target.value)} /></label>
    <div className="form-actions"><button type="button" className="outline-button" disabled={pending} onClick={onDraft}>임시 저장</button><button type="submit" className="primary" disabled={pending}>경험 저장 <Check size={17} /></button></div>
  </form>;
}

export function Projects({ activeId, onActive, activeQuestionId, onActiveQuestion, activeJobId, onActiveJob, onAddExperience, newRequest = 0, prefill }: {
  activeId: string | null;
  onActive: (id: string | null) => void;
  activeQuestionId?: string | null;
  onActiveQuestion?: (id: string | null) => void;
  activeJobId?: string | null;
  onActiveJob?: (id: string | null) => void;
  onAddExperience: () => void;
  newRequest?: number;
  prefill?: { company: string; role: string };
}) {
  const queryClient = useQueryClient();
  const projects = useProjects(); const project = useProject(activeId); const questions = useQuestions(activeId); const companies = useCompanies();
  const jobs = useJobs({ limit: 10 });
  const selectedQuestion = questions.data?.find((item) => item.id === activeQuestionId) ?? null;
  const [creating, setCreating] = useState(false); const [editing, setEditing] = useState(false);
  const [companyId, setCompanyId] = useState(""); const [role, setRole] = useState(""); const [questionText, setQuestionText] = useState("");
  const [editingQuestionId, setEditingQuestionId] = useState<string | null>(null); const [editingQuestion, setEditingQuestion] = useState("");
  const [conflict, setConflict] = useState<ConflictDescription | null>(null);
  useEffect(() => { if (newRequest) { setCreating(true); setRole(prefill?.role ?? ""); } }, [newRequest, prefill]);
  useEffect(() => { const match = companies.data?.items?.find((item) => item.display_name === prefill?.company); if (match) setCompanyId(match.id); }, [companies.data, prefill]);
  useEffect(() => { if (project.data && editing) setRole(project.data.role_name); }, [editing, project.data]);

  const create = useMutation({
    mutationFn: async () => {
      if (!companyId || !role.trim()) throw new Error("기업과 지원 직무를 입력해 주세요.");
      const company = companies.data?.items?.find((item) => item.id === companyId);
      const created = await createProjectsApi().create({ company_id: companyId, title: `${company?.display_name ?? "지원"} ${role.trim()}`, role_name: role.trim(), organization_name: company?.display_name ?? null }, createIdempotentIntent({ companyId, role }).key);
      const prompts = questionText.split("\n").map((value) => value.trim()).filter(Boolean);
      try {
        await Promise.all(prompts.map((prompt, displayOrder) => createQuestionsApi().create(created.id, { prompt, display_order: displayOrder, source: "USER_INPUT" }, createIdempotentIntent({ projectId: created.id, prompt, displayOrder }).key)));
      } catch { toast.error("프로젝트는 저장됐지만 일부 문항을 저장하지 못했습니다. 프로젝트에서 이어서 입력할 수 있습니다."); }
      return created;
    },
    onSuccess: async (created) => { setCreating(false); setQuestionText(""); await queryClient.invalidateQueries({ queryKey: projectQueryKeys.all() }); onActive(created.id); toast.success("지원 프로젝트를 서버에 저장했어요."); },
    onError: (error) => toast.error(error instanceof Error ? error.message : "프로젝트를 저장하지 못했습니다."),
  });
  const updateProject = useMutation({
    mutationFn: async () => {
      if (!project.data) throw new Error("프로젝트를 불러오지 못했습니다.");
      return createProjectsApi().update(project.data.id, project.data.current_version, { role_name: role.trim(), change_reason: "사용자 화면에서 프로젝트 수정" }, createIdempotentIntent({ id: project.data.id, role }).key);
    },
    onSuccess: async () => { setEditing(false); await Promise.all([queryClient.invalidateQueries({ queryKey: projectQueryKeys.detail(activeId!) }), queryClient.invalidateQueries({ queryKey: projectQueryKeys.lists() })]); toast.success("프로젝트를 수정했어요."); },
    onError: (error) => { const next = classifyConflict(error); if (next) setConflict(next); else toast.error(error instanceof Error ? error.message : "프로젝트를 수정하지 못했습니다."); },
  });
  const updateQuestion = useMutation({
    mutationFn: async () => {
      const value = questions.data?.find((item) => item.id === editingQuestionId);
      if (!value || !activeId) throw new Error("문항을 불러오지 못했습니다.");
      return createQuestionsApi().update(value.id, value.current_version, { prompt: editingQuestion.trim() }, createIdempotentIntent({ id: value.id, prompt: editingQuestion }).key);
    },
    onSuccess: async () => { setEditingQuestionId(null); await queryClient.invalidateQueries({ queryKey: questionQueryKeys.list(activeId!) }); toast.success("문항을 수정했어요."); },
    onError: (error) => { const next = classifyConflict(error); if (next) setConflict(next); else toast.error(error instanceof Error ? error.message : "문항을 수정하지 못했습니다."); },
  });
  const reload = async () => { setConflict(null); await Promise.all([project.refetch(), questions.refetch()]); };

  return <>
    {!activeId ? <>
      <Heading eyebrow="APPLICATION PROJECTS" title="다음 기회를 준비하는 곳" description="지원 건별 문항을 PostgreSQL에 저장하고 언제든 이어서 준비하세요."><button className="primary" onClick={() => setCreating(true)}><Plus size={18} /> 새 지원 프로젝트</button></Heading>
      {activeJobId ? <><button className="back-button" onClick={() => onActiveJob?.(null)}><ArrowLeft size={16} /> 분석 목록</button><JobPanel jobId={activeJobId} /></> : (jobs.data?.items?.length ?? 0) > 0 && <section className="surface settings-card"><div className="section-heading"><h2>최근 분석 작업</h2><span className="count">{jobs.data?.items?.length}</span></div>{jobs.data?.items?.map((job) => <button className="question-item" key={job.id} onClick={() => onActiveJob?.(job.id)}><span>{job.status} · {job.dispatch_status}</span><strong>{job.job_type}</strong><small>{job.stage ?? "접수 단계"}</small></button>)}</section>}
      {projects.isLoading && <div className="empty-state" role="status">프로젝트를 불러오고 있습니다.</div>}
      <div className="projects-list">{projects.data?.items?.map((item) => <button className="surface project-list-item" key={item.id} onClick={() => onActive(item.id)}><span className="company-logo">{item.title.slice(0, 1)}</span><div><span className="muted">{item.status}</span><h2>{item.title}</h2><p>{item.current_step ?? "문항 입력 단계"}</p></div><ArrowUpRight size={21} /></button>)}</div>
      {!projects.isLoading && !(projects.data?.items?.length ?? 0) && <div className="empty-state"><h2>새로운 지원을 준비해 볼까요?</h2><button className="primary" onClick={() => setCreating(true)}>프로젝트 만들기</button></div>}
    </> : <>
      <button className="back-button" onClick={() => onActive(null)}><ArrowLeft size={16} /> 지원 프로젝트</button>
      <Heading eyebrow={`${project.data?.organization_name ?? "지원"} / APPLICATION WORKSPACE`} title={project.data?.role_name ?? "불러오는 중"}><button className="outline-button" onClick={() => setEditing(true)}>프로젝트 수정</button></Heading>
      <div className="project-workspace"><aside className="question-panel"><div className="section-heading"><h2>자기소개서 문항</h2><span className="count">{questions.data?.length ?? 0}</span></div>{questions.data?.map((item, index) => <div key={item.id}><button className={`question-item ${activeQuestionId === item.id ? "active" : ""}`} onClick={() => onActiveQuestion?.(item.id)}><span>QUESTION {String(index + 1).padStart(2, "0")}</span><strong>{item.prompt}</strong><small>추천 후보 보기</small></button><button className="text-button" onClick={() => { setEditingQuestionId(item.id); setEditingQuestion(item.prompt); }}>문항 수정</button></div>)}</aside><div className="candidate-area">{activeJobId ? <JobPanel jobId={activeJobId} /> : selectedQuestion ? <RecommendationWorkspace question={selectedQuestion} onAddExperience={onAddExperience} /> : <div className="job-panel"><h3>분석할 문항을 선택해 주세요</h3><p>문항을 선택하면 저장된 소재를 복구하거나 현재 버전으로 추천 분석을 시작할 수 있습니다.</p><button className="outline-button" onClick={onAddExperience}>경험 추가하기 <ArrowRight size={16} /></button></div>}</div></div>
    </>}
    <Dialog open={creating} onOpenChange={setCreating}><DialogContent><DialogHeader><DialogTitle>새 지원 프로젝트</DialogTitle><DialogDescription>식별 완료된 기업 카탈로그 항목을 선택합니다.</DialogDescription></DialogHeader><form className="experience-form" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><label>기업<select required aria-label="기업" value={companyId} onChange={(event) => setCompanyId(event.target.value)}><option value="">기업 선택</option>{companies.data?.items?.map((item) => <option value={item.id} key={item.id}>{item.display_name}</option>)}</select></label><label>지원 직무<input required value={role} onChange={(event) => setRole(event.target.value)} /></label><label>자기소개서 문항<textarea rows={4} value={questionText} onChange={(event) => setQuestionText(event.target.value)} placeholder="여러 문항은 줄을 바꿔 입력하세요." /></label><button className="primary" type="submit" disabled={create.isPending}>프로젝트 만들기 <ArrowRight size={17} /></button></form></DialogContent></Dialog>
    <Dialog open={editing} onOpenChange={setEditing}><DialogContent><DialogHeader><DialogTitle>프로젝트 수정</DialogTitle><DialogDescription>현재 버전을 기준으로 안전하게 수정합니다.</DialogDescription></DialogHeader><form className="experience-form" onSubmit={(event: FormEvent) => { event.preventDefault(); updateProject.mutate(); }}><label>지원 직무<input required value={role} onChange={(event) => setRole(event.target.value)} /></label><button className="primary" type="submit" disabled={updateProject.isPending}>변경 저장</button></form></DialogContent></Dialog>
    <Dialog open={Boolean(editingQuestionId)} onOpenChange={(open) => !open && setEditingQuestionId(null)}><DialogContent><DialogHeader><DialogTitle>문항 수정</DialogTitle><DialogDescription>서버의 현재 문항 버전을 기준으로 수정합니다.</DialogDescription></DialogHeader><form className="experience-form" onSubmit={(event) => { event.preventDefault(); updateQuestion.mutate(); }}><label>자기소개서 문항<textarea rows={5} value={editingQuestion} onChange={(event) => setEditingQuestion(event.target.value)} /></label><button className="primary" type="submit" disabled={updateQuestion.isPending}>문항 저장</button></form></DialogContent></Dialog>
    <ApiConflictDialog conflict={conflict} onClose={() => setConflict(null)} onReload={reload} />
  </>;
}

export function Settings() {
  return <><Heading eyebrow="PROFILE & SETTINGS" title="프로필 및 설정" /><section className="surface settings-card"><h2>개인 데이터 저장</h2><p>경험과 지원 프로젝트는 로그인 계정의 PostgreSQL 데이터로 관리됩니다. 브라우저 localStorage는 개인 데이터 권위로 사용하지 않습니다.</p></section></>;
}
