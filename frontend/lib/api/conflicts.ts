import { ApiError } from "./client";

export type ConflictDescription = Readonly<{
  kind: "version-conflict";
  message: string;
  canReload: true;
}>;

export function classifyConflict(error: unknown): ConflictDescription | null {
  if (!(error instanceof ApiError) || (error.status !== 409 && error.status !== 412)) return null;
  return { kind: "version-conflict", message: error.message, canReload: true };
}
