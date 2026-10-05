import { expect, test } from "@playwright/test";
import { newMigration } from "./helpers";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "stub agents ask questions in this mode");

test("a migration run goes through every stage and gate from the UI", async ({ page }) => {
  const operation = `e2eOp${Date.now()}`;
  await newMigration(page, "customer", operation);
  await page.getByRole("button", { name: "Start migration" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(operation);

  const approve = page.getByRole("button", { name: "Approve", exact: true });

  // Gate 1: analysis
  await expect(approve).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Reviewer").fill("e2e-reviewer");
  await approve.click();

  // Gate 2: design — reject once, then approve the re-run
  await expect(page.getByRole("heading", { name: /👤 Approve design/ })).toBeVisible({ timeout: 30_000 });
  await page.getByLabel(/Comments/).fill("Move the address tables into the customer boundary");
  await page.getByRole("button", { name: "Reject & re-run now" }).click();
  await expect(page.getByRole("button", { name: /×2/ })).toBeVisible({ timeout: 30_000 });
  await expect(approve).toBeVisible({ timeout: 30_000 });
  await approve.click();

  // Gates 3 and 4
  await expect(page.getByRole("heading", { name: /👤 Approve tests/ })).toBeVisible({ timeout: 30_000 });
  await approve.click();
  await expect(page.getByRole("heading", { name: /👤 Approve release/ })).toBeVisible({ timeout: 30_000 });
  await approve.click();

  await expect(page.getByText("Completed").first()).toBeVisible({ timeout: 30_000 });

  // The re-run design artifact reflects the reviewer's feedback.
  await page.getByRole("button", { name: /Modular monolith design/ }).click();
  await expect(page.getByText("Move the address tables into the customer boundary").first()).toBeVisible();
});
