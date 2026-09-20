import { useQuery } from "@tanstack/react-query";

import { createSelectionsApi } from "@/lib/api/selections";
import { personalQueryKeys } from "./query-client";

export const selectionQueryKeys = {
  all: () => [...personalQueryKeys.all(), "selections"] as const,
  current: (questionId: string) =>
    [...selectionQueryKeys.all(), "question", questionId] as const,
};

export function useMaterialSelection(questionId: string | null) {
  return useQuery({
    queryKey: selectionQueryKeys.current(questionId ?? "none"),
    queryFn: ({ signal }) => createSelectionsApi().getCurrentOrNull(questionId!, signal),
    enabled: Boolean(questionId),
    retry: false,
  });
}
