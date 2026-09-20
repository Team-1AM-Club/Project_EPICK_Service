export type ApiRequester = Readonly<{
  request: <T>(path: string, init?: RequestInit) => Promise<T>;
}>;

export function mutationHeaders(idempotencyKey: string, version?: number): Record<string, string> {
  return version === undefined
    ? { "Idempotency-Key": idempotencyKey }
    : { "Idempotency-Key": idempotencyKey, "If-Match": `"${version}"` };
}

export function withSearch(path: string, values: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}
