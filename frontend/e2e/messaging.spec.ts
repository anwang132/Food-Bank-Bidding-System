import { expect, test } from "@playwright/test";

import { ADMIN, NORTHSIDE, newUserPage, openNav, stamp, login } from "./helpers";

test("direct messages arrive live in both directions", async ({ browser }) => {
  const id = stamp();
  const north = await newUserPage(browser, NORTHSIDE);
  await north.goto("/messages");
  await north.getByRole("button", { name: "New message" }).click();
  const dialog = north.getByRole("dialog", { name: "New message" });
  await dialog.getByLabel("Search people").fill("Avery");
  await dialog.getByRole("button", { name: /Avery Thompson/ }).click();
  await expect(north.getByRole("heading", { level: 2, name: "Avery Thompson" })).toBeVisible();
  const threadPath = new URL(north.url()).pathname;

  const admin = await newUserPage(browser, ADMIN);
  await admin.goto(threadPath);
  await expect(admin.getByRole("heading", { level: 2, name: "Dana Whitfield" })).toBeVisible();

  const question = `Can Northside pick up the extra crates on Tuesday? (${id})`;
  await north.getByLabel("Message Avery Thompson").fill(question);
  await north.getByLabel("Message Avery Thompson").press("Enter");
  await expect(north.getByRole("log")).toContainText(question);
  // Delivered over the WebSocket: no reload on the admin's page.
  await expect(admin.getByRole("log")).toContainText(question, { timeout: 20_000 });

  const reply = `Yes, they're on the dock (${id})`;
  await admin.getByLabel("Message Dana Whitfield").fill(reply);
  await admin.getByRole("button", { name: "Send" }).click();
  await expect(north.getByRole("log")).toContainText(reply, { timeout: 20_000 });
});

test("groups can be created and show who created them", async ({ browser }) => {
  const id = stamp();
  const north = await newUserPage(browser, NORTHSIDE);
  await north.goto("/messages");
  await north.getByRole("button", { name: "New group" }).click();
  const dialog = north.getByRole("dialog", { name: "New group" });
  await dialog.getByLabel("Group name").fill(`Cold chain ${id}`);
  await dialog.getByRole("button", { name: /Maria Alvarez/ }).click();
  await dialog.getByRole("button", { name: /Morgan Ellis/ }).click();
  await dialog.getByRole("button", { name: "Create group" }).click();
  await expect(north.getByRole("heading", { level: 2, name: `Cold chain ${id}` })).toBeVisible();
  await expect(north.getByRole("log")).toContainText(
    `Dana Whitfield created Cold chain ${id} with Maria Alvarez and Morgan Ellis`,
  );
});

test("@narrow messages work at phone width", async ({ page }) => {
  await login(page, NORTHSIDE);
  await openNav(page);
  await page.getByRole("link", { name: /^Messages/ }).click();
  await page.getByRole("link", { name: /All food banks/ }).click();
  await expect(page.getByRole("heading", { level: 2, name: "All food banks" })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Conversations" })).toBeHidden();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
  await page.getByRole("link", { name: "All conversations" }).click();
  await expect(page.getByRole("complementary", { name: "Conversations" })).toBeVisible();
});
