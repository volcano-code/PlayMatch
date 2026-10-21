/** A per-tab opaque reference, never the query, password or service credentials. */
export interface RunReference {
  id: string;
  access_token: string | null;
  owner: string | null;
  saved_at: number;
}
export const RUN_REFERENCE_KEY = "playmatch.active-run.v1";
export const RUN_REFERENCE_TTL = 60 * 60 * 1000;

export function readRunReference(
  raw: string | null,
  owner: string | null,
  now = Date.now(),
): RunReference | null {
  if (!raw || raw.length > 2048) return null;
  try {
    const value = JSON.parse(raw) as RunReference;
    if (!value || typeof value !== "object" || value.owner !== owner) return null;
    if (typeof value.id !== "string" || !/^[0-9a-f-]{36}$/i.test(value.id)) return null;
    if (
      typeof value.saved_at !== "number" ||
      !Number.isFinite(value.saved_at) ||
      value.saved_at > now ||
      now - value.saved_at > RUN_REFERENCE_TTL
    )
      return null;
    if (owner === null) {
      if (
        typeof value.access_token !== "string" ||
        !/^[A-Za-z0-9_-]{32,128}$/.test(value.access_token)
      )
        return null;
    } else if (value.access_token !== null) return null;
    return { id: value.id, access_token: value.access_token, owner, saved_at: value.saved_at };
  } catch {
    return null;
  }
}

export function terminalRun(status: string): boolean {
  return ["completed", "partial", "failed", "cancelled"].includes(status);
}
