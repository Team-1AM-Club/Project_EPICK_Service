import type { PublicRuntimeConfig } from "./config";

export type ApiFieldError = Readonly<{ field: string; reason: string }>;
export type ApiErrorResponse = Readonly<{
  error: {
    code: string;
    message_ko: string;
    retryable: boolean;
    actions: string[];
    correlation_id: string;
    fields: ApiFieldError[];
  };
}>;

export class ApiError extends Error {
  readonly name = "ApiError";

  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable = false,
    readonly actions: string[] = [],
    readonly correlationId: string | null = null,
    readonly fields: ApiFieldError[] = [],
  ) {
    super(message);
  }
}

export type ApiClientOptions = Readonly<{
  config: PublicRuntimeConfig;
  getAccessToken: () => string | null;
  refreshAccessToken?: () => Promise<string | null>;
}>;

export type ApiRequestInit = RequestInit & Readonly<{ skipAuthRefresh?: boolean }>;

export function createApiClient(options: ApiClientOptions) {
  async function request<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
    assertPublicPath(path);
    return perform<T>(path, init, false);
  }

  async function perform<T>(
    path: string,
    init: ApiRequestInit,
    refreshed: boolean,
  ): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const token = options.getAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${options.config.w1ApiUrl}${path}`, {
      ...init,
      headers,
    });
    if (
      response.status === 401 &&
      !refreshed &&
      !init.skipAuthRefresh &&
      options.refreshAccessToken
    ) {
      const nextToken = await options.refreshAccessToken();
      if (nextToken) return perform<T>(path, init, true);
    }
    if (!response.ok) throw await decodeApiError(response);
    if (response.status === 204) return undefined as T;
    const contentType = response.headers.get("Content-Type") ?? "";
    if (!contentType.includes("application/json")) return undefined as T;
    return (await response.json()) as T;
  }

  return { request };
}

function assertPublicPath(path: string): void {
  if (!path.startsWith("/api/v1/") || path.startsWith("//")) {
    throw new Error("Only a relative W1 public API path is allowed");
  }
}

async function decodeApiError(response: Response): Promise<ApiError> {
  let payload: ApiErrorResponse | undefined;
  try {
    const candidate = (await response.json()) as Partial<ApiErrorResponse>;
    if (candidate.error?.code && candidate.error.message_ko) {
      payload = candidate as ApiErrorResponse;
    }
  } catch {
    // A proxy or network edge can return non-JSON. Keep the public fallback generic.
  }
  if (!payload) {
    return new ApiError(response.status, "REQUEST_FAILED", "요청을 처리할 수 없습니다.");
  }
  return new ApiError(
    response.status,
    payload.error.code,
    payload.error.message_ko,
    payload.error.retryable,
    payload.error.actions,
    payload.error.correlation_id,
    payload.error.fields,
  );
}
