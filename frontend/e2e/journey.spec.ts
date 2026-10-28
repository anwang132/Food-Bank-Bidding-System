import { expect, test } from "@playwright/test";

import {
  ADMIN,
  EASTSIDE,
  NORTHSIDE,
  createOpenRound,
  newUserPage,
  requestQuantity,
  stamp,
} from "./helpers";

test("@smoke request submission through allocation and results", async ({ browser }) => {
  const id = stamp();
  const lot = `E2E lentils ${id}`;
  const round = `E2E round ${id}`;

  const admin = await newUserPage(browser, ADMIN);
  const path = await createOpenRound(admin, { lot, round, stock: 100, reserve: 50 });

  // Member A requests 30, then edits to 35 (keeps accepted order).
  const north = await newUserPage(browser, NORTHSIDE);
  await north.goto(path);
  await requestQuantity(north, lot, 30);
  await expect(
    north.getByRole("status").filter({ hasText: `Requested 30 cases of ${lot}` }),
  ).toBeVisible();
  await north.getByRole("button", { name: "Edit" }).click();
  await requestQuantity(north, lot, 35);
  await expect(north.getByRole("status").filter({ hasText: "Updated" })).toBeVisible();

  // Member B requests 40 and keeps the page open (no reloads from here on).
  const east = await newUserPage(browser, EASTSIDE);
  await east.goto(path);
  await requestQuantity(east, lot, 40);
  await expect(east.getByRole("status").filter({ hasText: "Requested 40 cases" })).toBeVisible();
  // B sees only its own request, never Northside's 35.
  const eastLots = east.getByRole("region", { name: "Inventory in this round" });
  await expect(eastLots).toContainText("40 cases");
  await expect(eastLots).not.toContainText("35 cases");

  // Admin sees both requests live, closes, previews and finalizes.
  await expect(admin.getByRole("region", { name: "All requests" })).toContainText("Eastside");
  await admin.getByRole("button", { name: "Close requests" }).click();
  await admin.getByRole("button", { name: "Close round" }).click();
  const preview = admin.getByRole("region", { name: `Allocation for ${lot}` });
  await expect(preview).toContainText("Northside Community Pantry");
  await expect(preview.getByRole("row", { name: /Northside/ })).toContainText("35 cases");
  await expect(preview.getByRole("row", { name: /Eastside/ })).toContainText("15 cases");

  await admin.getByRole("button", { name: "Finalize allocation" }).click();
  await admin.getByRole("button", { name: /Finalize — this cannot be undone/ }).click();
  await expect(admin.getByText("Allocation finalized")).toBeVisible();

  // Member B's open page updates via realtime without reload.
  await expect(east.getByText("Allocation is final")).toBeVisible({ timeout: 20_000 });
  await east.getByRole("link", { name: "View results" }).click();
  const eastResult = east.getByRole("region", { name: `Allocation for ${lot}` });
  await expect(eastResult).toContainText("15 cases");
  await expect(eastResult).toContainText("Partly filled: 15 of 40 cases");
  await expect(eastResult).not.toContainText("Northside");

  await north.goto(`${path}/results`);
  await expect(north.getByRole("region", { name: `Allocation for ${lot}` })).toContainText(
    "Fully filled: 35 of 35 cases",
  );

  // The audit log records the finalization.
  await admin.goto("/admin/audit");
  await admin.getByLabel("Action").selectOption("round.finalize");
  await expect(admin.getByRole("region", { name: "Audit records" })).toContainText(
    "round.finalize",
  );
});
