import type { Activity, ActivityCreate, ActivityUpdate } from "./activities";
import type { Episode, EpisodeCreate, EpisodeUpdate } from "./episodes";

export type ExperienceFormValue = Readonly<{
  title: string;
  organization: string;
  activityType: string;
  startDate: string;
  endDate: string;
  role: string;
  outcomeStatus: string;
  outcomeSummary: string;
  story: string;
}>;

export const emptyExperienceForm: ExperienceFormValue = {
  title: "",
  organization: "",
  activityType: "PROJECT",
  startDate: "",
  endDate: "",
  role: "",
  outcomeStatus: "IN_PROGRESS",
  outcomeSummary: "",
  story: "",
};

function fieldValue(value: string) {
  const normalized = value.trim();
  return normalized
    ? { value: normalized, availability: "PROVIDED" as const }
    : { value: null, availability: "NOT_PROVIDED" as const };
}

export function toActivityCreate(form: ExperienceFormValue): ActivityCreate {
  const hasPeriod = Boolean(form.startDate || form.endDate);
  return {
    title: form.title.trim(),
    organization: fieldValue(form.organization),
    activity_type: form.activityType || null,
    period: {
      start_date: form.startDate || null,
      end_date: form.endDate || null,
      precision: hasPeriod ? "DAY" : null,
      availability: hasPeriod ? "PROVIDED" : "NOT_PROVIDED",
    },
    role: fieldValue(form.role),
    outcome: {
      status: form.outcomeStatus || null,
      summary: fieldValue(form.outcomeSummary),
    },
    usage_enabled: true,
  };
}

export function toActivityUpdate(form: ExperienceFormValue): ActivityUpdate {
  return { ...toActivityCreate(form), change_reason: "사용자 화면에서 경험 수정" };
}

export function toEpisodeCreate(form: ExperienceFormValue): EpisodeCreate {
  return {
    title: form.title.trim(),
    original_narrative: form.story.trim() || null,
    usage_enabled: true,
  };
}

export function toEpisodeUpdate(form: ExperienceFormValue): EpisodeUpdate {
  return { ...toEpisodeCreate(form), change_reason: "사용자 화면에서 경험 사건 수정" };
}

export function fromExperience(activity: Activity, episode?: Episode | null): ExperienceFormValue {
  return {
    title: activity.title,
    organization: activity.organization.value ?? "",
    activityType: activity.activity_type ?? "PROJECT",
    startDate: activity.period.start_date ?? "",
    endDate: activity.period.end_date ?? "",
    role: activity.role.value ?? "",
    outcomeStatus: activity.outcome.status ?? "IN_PROGRESS",
    outcomeSummary: activity.outcome.summary?.value ?? "",
    story: episode?.original_narrative ?? "",
  };
}

type ActivityWriter = Readonly<{
  create: (body: ActivityCreate, key: string) => Promise<{ id: string; current_version: number }>;
  update: (
    id: string,
    version: number,
    body: ActivityUpdate,
    key: string,
  ) => Promise<{ id: string; current_version: number }>;
  complete: (
    id: string,
    version: number,
    key: string,
  ) => Promise<{ id: string; current_version: number }>;
}>;

type EpisodeWriter = Readonly<{
  create: (
    activityId: string,
    body: EpisodeCreate,
    key: string,
  ) => Promise<{ id: string; current_version: number }>;
  update: (
    id: string,
    version: number,
    body: EpisodeUpdate,
    key: string,
  ) => Promise<{ id: string; current_version: number }>;
  complete: (
    id: string,
    version: number,
    key: string,
  ) => Promise<{ id: string; current_version: number }>;
}>;

export type ExistingExperience = Readonly<{
  activityId: string;
  activityVersion: number;
  episodeId?: string;
  episodeVersion?: number;
}>;

export class ExperienceSaveError extends Error {
  readonly name = "ExperienceSaveError";

  constructor(
    message: string,
    readonly activityId: string,
    readonly activityVersion: number,
    readonly phase: "episode" | "completion",
    readonly cause: unknown,
  ) {
    super(message);
  }
}

export async function persistExperience({
  form,
  mode,
  existing,
  activities,
  episodes,
  makeKey = () => crypto.randomUUID(),
}: {
  form: ExperienceFormValue;
  mode: "draft" | "complete";
  existing?: ExistingExperience;
  activities: ActivityWriter;
  episodes: EpisodeWriter;
  makeKey?: () => string;
}) {
  const activity = existing
    ? await activities.update(
        existing.activityId,
        existing.activityVersion,
        toActivityUpdate(form),
        makeKey(),
      )
    : await activities.create(toActivityCreate(form), makeKey());

  let episode: { id: string; current_version: number };
  try {
    episode = existing?.episodeId && existing.episodeVersion !== undefined
      ? await episodes.update(
          existing.episodeId,
          existing.episodeVersion,
          toEpisodeUpdate(form),
          makeKey(),
        )
      : await episodes.create(activity.id, toEpisodeCreate(form), makeKey());
  } catch (cause) {
    throw new ExperienceSaveError(
      "활동 초안은 저장됐지만 사건 기록은 저장하지 못했습니다.",
      activity.id,
      activity.current_version,
      "episode",
      cause,
    );
  }

  if (mode === "complete") {
    try {
      episode = await episodes.complete(episode.id, episode.current_version, makeKey());
      const completedActivity = await activities.complete(
        activity.id,
        activity.current_version,
        makeKey(),
      );
      return { activity: completedActivity, episode };
    } catch (cause) {
      throw new ExperienceSaveError(
        "초안은 저장됐지만 완료 처리하지 못했습니다.",
        activity.id,
        activity.current_version,
        "completion",
        cause,
      );
    }
  }
  return { activity, episode };
}
