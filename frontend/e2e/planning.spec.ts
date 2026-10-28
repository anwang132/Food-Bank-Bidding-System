import { expect, test } from "@playwright/test";

import { ADMIN, NORTHSIDE, createOpenRound, newUserPage, stamp } from "./helpers";

test("uploaded needs shape a plan that can be applied as requests", async ({ browser }) => {
  const id = stamp();
  const lot = `Planning chickpeas ${id}`;
  const round = `Planning round ${id}`;
  const admin = await newUserPage(browser, ADMIN);
  const path = await createOpenRound(admin, { lot, round, stock: 100, reserve: 50 });

  const north = await newUserPage(browser, NORTHSIDE);
  await north.goto("/planning?tab=documents");
  await north.getByLabel("Upload planning files").setInputFiles({
    name: `needs-${id}.csv`,
    mimeType: "text/csv",
    buffer: Buffer.from(`Item,Cases needed\n${lot},7\n`),
  });
  await expect(north.getByRole("status").filter({ hasText: "1 need found" })).toBeVisible();
  await expect(north.getByRole("listitem").filter({ hasText: `needs-${id}.csv` })).toContainText(
    lot,
  );

  await north.getByRole("tab", { name: "Sample plans" }).click();
  await expect(north.getByRole("button", { name: /Balanced/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const table = north.getByRole("region", { name: `Suggested requests for ${round}` });
  const row = table.getByRole("row", { name: new RegExp(lot) });
  await expect(row).toContainText("7");
  await expect(row).toContainText(`“${lot}: 7” in needs-${id}.csv`);

  await north.getByRole("button", { name: `Apply to ${round}` }).click();
  await expect(north.getByRole("status").filter({ hasText: "Submitted 1 request" })).toBeVisible();
  await expect(north.getByRole("button", { name: "Already applied" }).first()).toBeVisible();

  const roundId = path.split("/").pop();
  const detail = await (await north.request.get(`/api/rounds/${roundId}`)).json();
  expect(detail.requests.map((r: { quantity: number }) => r.quantity)).toEqual([7]);
});

test("preferences can be suggested from history and saved", async ({ browser }) => {
  const north = await newUserPage(browser, NORTHSIDE);
  await north.goto("/planning?tab=preferences");
  await north.getByRole("button", { name: "Suggest from history" }).click();
  await expect(north.getByRole("heading", { name: "Suggested from your history" })).toBeVisible();
  await north.getByRole("button", { name: "Use these" }).click();
  await north.getByRole("button", { name: "Save preferences" }).click();
  await expect(north.getByRole("status").filter({ hasText: "Preferences saved" })).toBeVisible();
});
