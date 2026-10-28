// Browser-visible update latency: time from issuing a committed write to the moment
// another user's page shows it. One observer clock (this Node process's
// performance.now()) brackets both ends; the write goes to the API process that
// does NOT host the page's WebSocket, so every sample crosses Redis.
//   npx playwright test --project perf
import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { expect, request, test } from "@playwright/test";

import { ADMIN, NORTHSIDE, PASSWORD, createOpenRound, newUserPage, stamp } from "./helpers";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ORIGIN = process.env.E2E_BASE_URL ?? "http://localhost:8080";
const SAMPLES = Number(process.env.PERF_SAMPLES ?? 50);
const WARMUP = 5;

test("@perf browser-visible update latency across processes", async ({ browser }) => {
  test.setTimeout(10 * 60_000);
  const id = stamp();
  const adminPage = await newUserPage(browser, ADMIN);
  const roundPath = await createOpenRound(adminPage, {
    lot: `Perf lot ${id}`,
    round: `Perf live ${id}`,
    stock: 10,
    reserve: 5,
  });
  const roundId = roundPath.split("/").pop()!;
  const page = await newUserPage(browser, NORTHSIDE);
  await page.goto(roundPath);
  const live = page.getByTestId("live-status");
  await expect(live).toHaveText(/Live updates on/);
  const pageInstance = await live.getAttribute("data-instance");
  const writer = pageInstance === "api-1" ? "http://localhost:8002" : "http://localhost:8001";

  const api = await request.newContext({ baseURL: writer, extraHTTPHeaders: { Origin: ORIGIN } });
  const login = await api.post("/api/auth/login", { data: { email: ADMIN, password: PASSWORD } });
  const csrf = (await login.json()).csrf_token;

  // Measure the probe's own overhead: a locator check on text that is already visible.
  const overhead: number[] = [];
  for (let i = 0; i < 20; i++) {
    const t0 = performance.now();
    await page.getByRole("heading", { level: 1 }).waitFor({ state: "visible" });
    overhead.push(performance.now() - t0);
  }

  const samples: number[] = [];
  for (let i = 0; i < WARMUP + SAMPLES; i++) {
    const text = `probe ${id} #${i}`;
    const detail = await (await api.get(`/api/rounds/${roundId}`)).json();
    const t0 = performance.now();
    const r = await api.patch(`/api/admin/rounds/${roundId}`, {
      data: { version: detail.public_version, description: text },
      headers: { "X-CSRF-Token": csrf },
    });
    expect(r.ok()).toBe(true);
    await page.getByText(text, { exact: true }).waitFor({ state: "visible", timeout: 30_000 });
    const elapsed = performance.now() - t0;
    if (i >= WARMUP) samples.push(elapsed);
  }

  const sorted = [...samples].sort((a, b) => a - b);
  const at = (p: number) => sorted[Math.ceil((p / 100) * sorted.length) - 1]!;
  const summary = {
    n: samples.length,
    p50_ms: at(50),
    p95_ms: samples.length >= 20 ? at(95) : null,
    max_ms: sorted[sorted.length - 1],
  };
  const out = {
    kind: "browser-visible-update",
    timestamp: new Date().toISOString(),
    clock: "Node performance.now() in the Playwright runner (single observer)",
    measured: "PATCH round description on the other API process → text visible in page",
    includes:
      "HTTP write + commit + Redis publish + WS delivery + refetch + React render + Playwright polling",
    page_instance: pageInstance,
    writer,
    warmup: WARMUP,
    probe_overhead_ms_median: [...overhead].sort((a, b) => a - b)[10],
    summary,
    samples_ms: samples,
    env: {
      node: process.version,
      platform: `${os.platform()} ${os.release()}`,
      cpus: os.cpus().length,
      cpu: os.cpus()[0]?.model,
    },
  };
  mkdirSync(path.join(ROOT, "perf/results"), { recursive: true });
  const file = path.join(
    ROOT,
    `perf/results/browser-${new Date().toISOString().replace(/[:.]/g, "")}.json`,
  );
  writeFileSync(file, JSON.stringify(out, null, 2));
  console.log(JSON.stringify(summary), "→", path.relative(ROOT, file));
});
