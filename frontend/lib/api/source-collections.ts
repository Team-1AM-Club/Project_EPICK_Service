import type { components } from "@/generated/w1-api";

import { getApiClient } from "./runtime";
import { type ApiRequester, mutationHeaders } from "./types";

export type SourceCollectionCreate = components["schemas"]["SourceCollectionCreateRequest"];
export type SourceCollectionAcceptance =
  components["schemas"]["SourceCollectionAcceptanceResponse"];
export type SourceCollectionProgress =
  components["schemas"]["SourceCollectionProgressResponse"];

export function createSourceCollectionsApi(client: ApiRequester = getApiClient()) {
  return {
    create: (projectId: string, body: SourceCollectionCreate, idempotencyKey: string) =>
      client.request<SourceCollectionAcceptance>(
        `/api/v1/application-projects/${projectId}/source-collections`,
        {
          method: "POST",
          headers: mutationHeaders(idempotencyKey),
          body: JSON.stringify(body),
        },
      ),
    progress: (projectId: string, jobId: string, signal?: AbortSignal) =>
      client.request<SourceCollectionProgress>(
        `/api/v1/application-projects/${projectId}/source-collections/${jobId}`,
        { signal },
      ),
    latest: (projectId: string, signal?: AbortSignal) =>
      client.request<SourceCollectionProgress>(
        `/api/v1/application-projects/${projectId}/source-collections`,
        { signal },
      ),
  };
}

export type SourceCollectionsApi = ReturnType<typeof createSourceCollectionsApi>;
