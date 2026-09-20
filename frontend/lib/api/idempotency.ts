export type IdempotentIntent = Readonly<{
  key: string;
  requestFingerprint: string;
}>;

export function createIdempotentIntent(payload: unknown): IdempotentIntent {
  return {
    key: crypto.randomUUID(),
    requestFingerprint: canonicalJson(payload),
  };
}

export function reuseIntent(intent: IdempotentIntent, payload: unknown): string {
  if (intent.requestFingerprint !== canonicalJson(payload)) {
    throw new Error("An idempotency key cannot be reused for a different request");
  }
  return intent.key;
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(sortValue(value));
}

function sortValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, sortValue(item)]),
    );
  }
  return value;
}
