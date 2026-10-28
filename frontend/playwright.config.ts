import { defineConfig, devices } from "@playwright/test";

// Runs against the docker compose stack (nginx → two API processes).
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"] },
      grepInvert: /@narrow|@realtime|@perf/,
    },
    { name: "narrow", use: { ...devices["Pixel 7"] }, grep: /@narrow/ },
    // Controls docker compose (stops Redis); run explicitly with --project realtime.
    { name: "realtime", use: { ...devices["Desktop Chrome"] }, grep: /@realtime/ },
    { name: "perf", use: { ...devices["Desktop Chrome"] }, grep: /@perf/ },
  ],
});
