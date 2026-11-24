import { expect, test } from "@playwright/test";
import { approveGate, newMigration, rejectGate } from "./helpers";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "stub agents ask questions in this mode");

test("a migration run goes through every stage and gate from the UI", async ({ page }) => {
  const operation = `e2eOp${Date.now()}`;
  await newMigration(page, "customer", operation);
  await page.getByRole("button", { name: "Start migration" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(operation);

  // Gate 1: analysis
  await approveGate(page);

  // Gate 2: design — reject once, then approve the re-run
  await expect(page.getByRole("heading", { name: /👤 Approve design/ })).toBeVisible({ timeout: 30_000 });
  await rejectGate(page, "Move the address tables into the customer boundary");
  await expect(page.getByRole("button", { name: /×2/ })).toBeVisible({ timeout: 30_000 });
  await approveGate(page);

  // Gates 3 and 4
  await expect(page.getByRole("heading", { name: /👤 Approve tests/ })).toBeVisible({ timeout: 30_000 });
  await approveGate(page);
  await expect(page.getByRole("heading", { name: /👤 Approve release/ })).toBeVisible({ timeout: 30_000 });
  await approveGate(page);

  await expect(page.getByText("Completed").first()).toBeVisible({ timeout: 30_000 });

  // The re-run design artifact reflects the reviewer's feedback.
  await page.getByRole("button", { name: /Linked-domain contracts/ }).click(); // a rejection re-runs from the gate's first reviewed step
  await expect(page.getByText("Move the address tables into the customer boundary").first()).toBeVisible();
});
