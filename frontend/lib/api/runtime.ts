import { getAuthClient } from "./auth";
import { createApiClient } from "./client";
import { getPublicRuntimeConfig } from "./config";
import { authStore } from "../state/auth-store";

let singleton: ReturnType<typeof createApiClient> | undefined;

export function getApiClient() {
  singleton ??= createApiClient({
    config: getPublicRuntimeConfig(),
    getAccessToken: () => authStore.getSnapshot().accessToken,
    refreshAccessToken: () => getAuthClient().refresh(),
  });
  return singleton;
}
