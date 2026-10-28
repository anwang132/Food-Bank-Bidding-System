import { expect, test } from "@playwright/test";

import {
  ADMIN,
  EASTSIDE,
  NORTHSIDE,
  openNav,
  VIEWER,
  createOpenRound,
  login,
  newUserPage,
  requestQuantity,
  stamp,
} from "./helpers";

test("@smoke unauthenticated users are sent to sign in", async ({ page }) => {
  await page.goto("/admin/audit");
  await expect(page).toHaveURL(/\/login$/);
  const r = await page.request.get("/api/rounds");
  expect(r.status()).toBe(401);
});

test("@smoke members cannot use admin screens or APIs", async ({ page }) => {
  await login(page, NORTHSIDE);
  await expect(page.getByRole("link", { name: "Audit log" })).toHaveCount(0);
  await page.goto("/admin/inventory");
  await expect(page.getByText("Your account does not have access to this page.")).toBeVisible();
  // The server enforces it independently of the UI.
  for (const path of ["/api/admin/audit", "/api/admin/lots", "/api/admin/users"]) {
    expect((await page.request.get(path)).status()).toBe(403);
  }
  const csrf = (await (await page.request.get("/api/auth/me")).json()).csrf_token;
  const r = await page.request.post("/api/admin/organizations", {
    data: { name: "Sneaky Org", default_weight: 1, default_priority: 1 },
    headers: { "X-CSRF-Token": csrf },
  });
  expect(r.status()).toBe(403);
});

test("members cannot see or change another organization's requests", async ({ browser }) => {
  const id = stamp();
  const lot = `E2E rice ${id}`;
  const admin = await newUserPage(browser, ADMIN);
  const path = await createOpenRound(admin, {
    lot,
    round: `E2E isolation ${id}`,
    stock: 20,
    reserve: 10,
  });

  const east = await newUserPage(browser, EASTSIDE);
  await east.goto(path);
  await requestQuantity(east, lot, 7);
  await expect(east.getByRole("status").filter({ hasText: "Requested 7" })).toBeVisible();
  const roundId = path.split("/").pop();
  const eastDetail = await (await east.request.get(`/api/rounds/${roundId}`)).json();
  const eastRequest = eastDetail.requests[0];

  const north = await newUserPage(browser, NORTHSIDE);
  await north.goto(path);
  await expect(north.getByText("No request").first()).toBeHidden(); // own form shown instead
  const detail = await (await north.request.get(`/api/rounds/${roundId}`)).json();
  expect(detail.requests).toEqual([]);
  const csrf = (await (await north.request.get("/api/auth/me")).json()).csrf_token;
  const r = await north.request.patch(`/api/requests/${eastRequest.id}`, {
    data: { quantity: 1, version: eastRequest.version },
    headers: { "X-CSRF-Token": csrf },
  });
  expect(r.status()).toBe(404);
});

test("viewers see only public summaries", async ({ page }) => {
  await login(page, VIEWER);
  await expect(page).toHaveURL(/\/public$/);
  await expect(page.getByRole("heading", { name: "Public summary" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Rounds", exact: true })).toHaveCount(0);
  expect((await page.request.get("/api/rounds")).status()).toBe(403);
  const body = await (await page.request.get("/api/public/rounds")).text();
  expect(body).not.toContain("Northside");
  expect(body).not.toContain("organization_id");
});

test("@narrow round page is usable at phone width", async ({ page }) => {
  await login(page, NORTHSIDE);
  await openNav(page);
  await page.getByRole("link", { name: "Rounds", exact: true }).click();
  await page.getByRole("link", { name: "Week 45 family staples" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await expect(page.getByRole("region", { name: "Inventory in this round" })).toBeVisible();
});
