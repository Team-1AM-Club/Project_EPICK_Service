import type { PublicRuntimeConfig } from "./config";
import { getPublicRuntimeConfig } from "./config";
import { authStore, type AuthStoreApi } from "../state/auth-store";

type AccessTokenResponse = Readonly<{
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
}>;

export function createAuthClient(config: PublicRuntimeConfig, store: AuthStoreApi) {
  let refreshInFlight: Promise<string | null> | null = null;

  function loginUrl(returnTo = "/"): string {
    const safePath = safeAuthReturnPath(returnTo);
    return `${config.w1ApiUrl}/api/v1/auth/google/start?return_to=${encodeURIComponent(safePath)}`;
  }

  function refresh(): Promise<string | null> {
    if (refreshInFlight) return refreshInFlight;
    refreshInFlight = fetch(`${config.w1ApiUrl}/api/v1/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json" },
      cache: "no-store",
    })
      .then(async (response) => {
        if (response.status === 401) {
          store.clear();
          return null;
        }
        if (!response.ok) throw new Error("Authentication refresh failed");
        const payload = (await response.json()) as AccessTokenResponse;
        if (
          payload.token_type !== "Bearer" ||
          !payload.access_token ||
          !Number.isInteger(payload.expires_in) ||
          payload.expires_in <= 0
        ) {
          throw new Error("Authentication refresh response is invalid");
        }
        store.setAccessToken(payload.access_token, payload.expires_in);
        return payload.access_token;
      })
      .catch((error) => {
        store.clear();
        throw error;
      })
      .finally(() => {
        refreshInFlight = null;
      });
    return refreshInFlight;
  }

  async function logout(): Promise<void> {
    try {
      await fetch(`${config.w1ApiUrl}/api/v1/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
    } finally {
      store.clear();
    }
  }

  return { loginUrl, refresh, logout };
}

let singleton: ReturnType<typeof createAuthClient> | undefined;

export function getAuthClient() {
  singleton ??= createAuthClient(getPublicRuntimeConfig(), authStore);
  return singleton;
}

export function safeAuthReturnPath(value: string): string {
  if (!value.startsWith("/") || value.startsWith("//")) {
    throw new Error("Authentication return path must be relative");
  }
  const parsed = new URL(value, "https://app.local");
  if (parsed.origin !== "https://app.local" || parsed.pathname.startsWith("/api/v1/auth")) {
    throw new Error("Authentication return path is not allowed");
  }
  return `${parsed.pathname}${parsed.search}${parsed.hash}`;
}
