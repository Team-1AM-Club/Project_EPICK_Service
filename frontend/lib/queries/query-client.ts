import { QueryClient } from "@tanstack/react-query";

export class ClientGeneration {
  #value = 0;

  current(): number {
    return this.#value;
  }

  bump(): number {
    this.#value += 1;
    return this.#value;
  }

  isCurrent(captured: number): boolean {
    return captured === this.#value;
  }
}

export const clientGeneration = new ClientGeneration();

export function createEpikQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        gcTime: 5 * 60_000,
        retry: (failureCount, error) => {
          const status = (error as { status?: number }).status;
          return status === 401 || status === 403 || status === 404
            ? false
            : failureCount < 2;
        },
        refetchOnWindowFocus: false,
      },
      mutations: { retry: false },
    },
  });
}

export const personalQueryKeys = {
  all: (generation = clientGeneration.current()) => ["personal", generation] as const,
  currentUser: (generation = clientGeneration.current()) =>
    [...personalQueryKeys.all(generation), "current-user"] as const,
  home: (generation = clientGeneration.current()) =>
    [...personalQueryKeys.all(generation), "home"] as const,
  resumeItems: (generation = clientGeneration.current()) =>
    [...personalQueryKeys.all(generation), "resume-items"] as const,
};

export async function clearPrivateQueryState(queryClient: QueryClient): Promise<void> {
  clientGeneration.bump();
  await queryClient.cancelQueries({ queryKey: ["personal"] });
  queryClient.removeQueries({ queryKey: ["personal"] });
}
