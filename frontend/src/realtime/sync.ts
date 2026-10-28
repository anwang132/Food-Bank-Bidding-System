// Pure decision logic for the recovery protocol (docs/DECISIONS.md D7/D8).

export type Scope = "public" | "org" | "admin";

export interface KnownVersions {
  public_version?: number | null;
  org_version?: number | null;
  version?: number | null;
}

export interface ServerEvent {
  type: string;
  scope?: Scope | "chat";
  round_id?: number;
  channel_id?: number;
  version?: number;
  reason?: string;
}

const FIELD: Record<Scope, keyof KnownVersions> = {
  public: "public_version",
  org: "org_version",
  admin: "version",
};

/** True when an event announces a version newer than what the client holds.
 *  Unknown local state counts as stale; older or equal versions (duplicates,
 *  out-of-order delivery) are ignored. */
export function isStale(known: KnownVersions | undefined, scope: Scope, version: number): boolean {
  if (!known) return true;
  const current = known[FIELD[scope]];
  return current === undefined || current === null || version > current;
}

/** Compare a periodic /api/sync/versions response with cached state; returns the
 *  ids of rounds that must be refetched. Rounds absent from the cache are stale. */
export function staleRounds(
  server: (KnownVersions & { round_id: number })[],
  cached: (id: number) => KnownVersions | undefined,
): number[] {
  const out: number[] = [];
  for (const s of server) {
    const known = cached(s.round_id);
    const scopes: Scope[] = ["public", "org", "admin"];
    if (
      scopes.some((scope) => {
        const v = s[FIELD[scope]];
        return typeof v === "number" && isStale(known, scope, v);
      })
    ) {
      out.push(s.round_id);
    }
  }
  return out;
}

/** Capped exponential backoff with full jitter. */
export function backoffDelay(attempt: number, random = Math.random): number {
  const cap = Math.min(10_000, 500 * 2 ** attempt);
  return Math.round(cap / 2 + (random() * cap) / 2);
}
