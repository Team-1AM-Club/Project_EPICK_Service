export type ResultMode = "synthetic" | "live";

export type PublicRuntimeConfig = Readonly<{
  w1ApiUrl: string;
  jobPollMs: number;
  resultMode: ResultMode;
}>;

type PublicEnvironment = Readonly<Record<string, string | undefined>>;

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

export function parsePublicRuntimeConfig(env: PublicEnvironment): PublicRuntimeConfig {
  const rawUrl = env.NEXT_PUBLIC_W1_API_URL?.trim();
  if (!rawUrl) throw new Error("NEXT_PUBLIC_W1_API_URL is required");

  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new Error("NEXT_PUBLIC_W1_API_URL must be an absolute URL");
  }
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error("W1 API URL must use HTTP or HTTPS");
  }
  if (url.protocol !== "https:" && !LOCAL_HOSTS.has(url.hostname)) {
    throw new Error("W1 API URL must use HTTPS outside localhost");
  }
  if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("W1 API URL must be an origin without credentials, path, query, or fragment");
  }

  const jobPollMs = Number(env.NEXT_PUBLIC_EPICK_JOB_POLL_MS ?? "3000");
  if (!Number.isInteger(jobPollMs) || jobPollMs < 1000 || jobPollMs > 60_000) {
    throw new Error("NEXT_PUBLIC_EPICK_JOB_POLL_MS must be between 1000 and 60000");
  }

  const resultMode = env.NEXT_PUBLIC_EPICK_RESULT_MODE ?? "synthetic";
  if (resultMode !== "synthetic" && resultMode !== "live") {
    throw new Error("NEXT_PUBLIC_EPICK_RESULT_MODE must be synthetic or live");
  }

  return {
    w1ApiUrl: url.origin,
    jobPollMs,
    resultMode,
  };
}

let cachedConfig: PublicRuntimeConfig | undefined;

export function getPublicRuntimeConfig(): PublicRuntimeConfig {
  cachedConfig ??= parsePublicRuntimeConfig({
    NEXT_PUBLIC_W1_API_URL: process.env.NEXT_PUBLIC_W1_API_URL,
    NEXT_PUBLIC_EPICK_JOB_POLL_MS: process.env.NEXT_PUBLIC_EPICK_JOB_POLL_MS,
    NEXT_PUBLIC_EPICK_RESULT_MODE: process.env.NEXT_PUBLIC_EPICK_RESULT_MODE,
  });
  return cachedConfig;
}
