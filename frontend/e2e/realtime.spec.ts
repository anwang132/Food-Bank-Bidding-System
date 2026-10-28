// Browser-visible live updates against the compose stack. Stops/starts the compose
// `redis` service, so it runs only in the "realtime" project:
//   npx playwright test --project realtime
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, request, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

import { ADMIN, NORTHSIDE, PASSWORD, createOpenRound, newUserPage, stamp } from "./helpers";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:8080";
const DIRECT: Record<string, string> = {
  "api-1": "http://localhost:8001",
  "api-2": "http://localhost:8002",
};
const RECOVERY_BOUND_MS = 20_000; // resync interval (15 s) + one fetch

function compose(...args: string[]): void {
  execFileSync("docker", ["compose", ...args], { cwd: ROOT, stdio: "ignore" });
}

async function adminOn(base: string): Promise<{ api: APIRequestContext; csrf: string }> {
  const api = await request.newContext({ baseURL: base, extraHTTPHeaders: { Origin: ORIGIN } });
  const r = await api.post("/api/auth/login", { data: { email: ADMIN, password: PASSWORD } });
  expect(r.ok()).toBe(true);
  return { api, csrf: (await r.json()).csrf_token };
}

async function setDescription(
  admin: { api: APIRequestContext; csrf: string },
  id: string,
  text: string,
) {
  const detail = await (await admin.api.get(`/api/rounds/${id}`)).json();
  const r = await admin.api.patch(`/api/admin/rounds/${id}`, {
    data: { version: detail.public_version, description: text },
    headers: { "X-CSRF-Token": admin.csrf },
  });
  expect(r.ok()).toBe(true);
}

/** Reload until the page's WebSocket is served by `instance`. */
async function pinTo(page: Page, instance: string): Promise<void> {
  const live = page.getByTestId("live-status");
  for (let i = 0; i < 10; i++) {
    await expect(live).toHaveText(/Live updates on/);
    if ((await live.getAttribute("data-instance")) === instance) return;
    await page.reload();
  }
  throw new Error(`page never connected to ${instance}`);
}

test("@realtime pages on different processes converge, with and without Redis", async ({
  browser,
}) => {
  const id = stamp();
  const adminPage = await newUserPage(browser, ADMIN);
  const roundPath = await createOpenRound(adminPage, {
    lot: `RT beans ${id}`,
    round: `RT round ${id}`,
    stock: 10,
    reserve: 5,
  });
  const roundId = roundPath.split("/").pop()!;

  const p1 = await newUserPage(browser, NORTHSIDE);
  const p2 = await newUserPage(browser, NORTHSIDE);
  await p1.goto(roundPath);
  await p2.goto(roundPath);
  await pinTo(p1, "api-1");
  await pinTo(p2, "api-2");

  // A write on api-1 reaches the page connected to api-2 (and vice versa) via Redis.
  const admin1 = await adminOn(DIRECT["api-1"]!);
  const admin2 = await adminOn(DIRECT["api-2"]!);
  await setDescription(admin1, roundId, `written on api-1 ${id}`);
  await expect(p2.getByText(`written on api-1 ${id}`)).toBeVisible({ timeout: 5_000 });
  await setDescription(admin2, roundId, `written on api-2 ${id}`);
  await expect(p1.getByText(`written on api-2 ${id}`)).toBeVisible({ timeout: 5_000 });

  // Redis down: the api-2 page's socket stays open but misses the api-1 write.
  // It must still converge within the bound through the periodic version check.
  compose("stop", "redis");
  try {
    const started = Date.now();
    await setDescription(admin1, roundId, `written during outage ${id}`);
    await expect(p2.getByText(`written during outage ${id}`)).toBeVisible({
      timeout: RECOVERY_BOUND_MS,
    });
    expect(Date.now() - started).toBeLessThan(RECOVERY_BOUND_MS);
  } finally {
    compose("start", "redis");
  }

  // After Redis returns, live delivery resumes without reloading.
  await expect
    .poll(async () => (await (await admin1.api.get("/readyz")).json()).redis, { timeout: 15_000 })
    .toBe("ok");
  await setDescription(admin1, roundId, `after recovery ${id}`);
  await expect(p2.getByText(`after recovery ${id}`)).toBeVisible({ timeout: 5_000 });
});
