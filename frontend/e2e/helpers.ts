import { expect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";

export const PASSWORD = process.env.E2E_PASSWORD ?? "foodbank-demo";
export const ADMIN = "admin@example.org";
export const NORTHSIDE = "northside@example.org";
export const EASTSIDE = "eastside@example.org";
export const VIEWER = "viewer@example.org";

export async function login(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  const signedIn = page.getByTestId("app-shell");
  const failure = page.getByRole("alert");
  await expect(signedIn.or(failure)).toBeVisible();
  if (await failure.isVisible()) {
    // e.g. the per-IP login limit when suites are run in a tight loop
    throw new Error(`Sign-in as ${email} failed: ${await failure.textContent()}`);
  }
}

export async function newUserPage(browser: Browser, email: string): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await login(page, email);
  return page;
}

export function stamp(): string {
  return new Date().toISOString().replace(/\D/g, "").slice(4, 14);
}

/** Admin: create a lot and an open FCFS round offering `reserve` units of it. */
export async function createOpenRound(
  admin: Page,
  opts: { lot: string; round: string; stock: number; reserve: number; strategy?: string },
): Promise<string> {
  await admin.goto("/admin/inventory");
  await admin.getByLabel("Item name").fill(opts.lot);
  await admin.getByLabel("Unit", { exact: true }).fill("cases");
  await admin.getByLabel("Quantity on hand").fill(String(opts.stock));
  await admin.getByRole("button", { name: "Add lot" }).click();
  await expect(admin.getByRole("status").filter({ hasText: `of ${opts.lot}` })).toBeVisible();

  await admin.goto("/admin/rounds/new");
  await admin.getByLabel("Name").fill(opts.round);
  await admin.getByRole("radio", { name: new RegExp(opts.strategy ?? "First come") }).check();
  await admin.getByRole("button", { name: "Create draft round" }).click();
  await expect(admin.getByRole("heading", { level: 1, name: opts.round })).toBeVisible();
  const url = admin.url();

  const value = await admin
    .locator("#add-lot option", { hasText: opts.lot })
    .getAttribute("value", { timeout: 10_000 });
  await admin.locator("#add-lot").selectOption(value!);
  await admin.locator("#add-qty").fill(String(opts.reserve));
  await admin.getByRole("button", { name: "Reserve" }).click();
  await expect(admin.getByRole("region", { name: "Reserved lots" })).toContainText(opts.lot);

  await admin.getByRole("button", { name: "Open for requests" }).click();
  await admin.getByRole("button", { name: "Open round" }).click();
  await expect(admin.getByText("Open for requests", { exact: true }).first()).toBeVisible();
  return new URL(url).pathname;
}

export async function requestQuantity(page: Page, lot: string, qty: number): Promise<void> {
  const input = page.getByLabel(new RegExp(`^Quantity of ${lot}`));
  await input.fill(String(qty));
  await page
    .getByRole("form", { name: `Request ${lot}` })
    .getByRole("button")
    .first()
    .click();
}

/** Phone layouts keep navigation in a drawer; open it when it is hidden. */
export async function openNav(page: Page): Promise<void> {
  const menu = page.getByRole("button", { name: "Open menu" });
  if (await menu.isVisible()) await menu.click();
}
