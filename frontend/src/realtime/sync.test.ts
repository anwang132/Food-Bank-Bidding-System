import { describe, expect, it } from "vitest";

import { backoffDelay, isStale, staleRounds } from "./sync";

describe("isStale", () => {
  it("treats unknown state as stale", () => {
    expect(isStale(undefined, "public", 1)).toBe(true);
    expect(isStale({ public_version: 3 }, "org", 1)).toBe(true);
  });

  it("ignores duplicate and out-of-order events", () => {
    expect(isStale({ public_version: 5 }, "public", 5)).toBe(false);
    expect(isStale({ public_version: 5 }, "public", 4)).toBe(false);
  });

  it("detects gaps: any newer version is stale, however many were skipped", () => {
    expect(isStale({ org_version: 2 }, "org", 3)).toBe(true);
    expect(isStale({ org_version: 2 }, "org", 9)).toBe(true);
    expect(isStale({ version: 2 }, "admin", 3)).toBe(true);
  });
});

describe("staleRounds", () => {
  it("returns rounds whose server counters moved past the cache", () => {
    const cache = new Map([
      [1, { public_version: 4, org_version: 2 }],
      [2, { public_version: 7, org_version: 1 }],
    ]);
    const server = [
      { round_id: 1, public_version: 4, org_version: 3 }, // private change missed
      { round_id: 2, public_version: 7, org_version: 1 }, // up to date
      { round_id: 3, public_version: 1 }, // new round never seen
    ];
    expect(staleRounds(server, (id) => cache.get(id))).toEqual([1, 3]);
  });
});

describe("backoffDelay", () => {
  it("grows exponentially and is capped at 10s", () => {
    expect(backoffDelay(0, () => 0)).toBe(250);
    expect(backoffDelay(3, () => 1)).toBe(4000);
    expect(backoffDelay(20, () => 1)).toBe(10000);
  });
});
