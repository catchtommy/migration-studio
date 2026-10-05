import { expect, test } from "@playwright/test";
import { newMigration } from "./helpers";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "stub agents ask questions in this mode");

test("an approved integration design fills the contracts catalogue and the dependency views", async ({ page }) => {
  const operation = `xdOp${Date.now()}`;
  await newMigration(page, "loan", operation);
  await page.getByLabel("Workflow").selectOption({ label: "Standard migration" });
  await page.getByRole("button", { name: "Start migration" }).click();

  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await expect(page.getByRole("heading", { name: /👤 Approve analysis/ })).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Reviewer").fill("e2e-reviewer");
  await approve.click();
  await expect(page.getByRole("heading", { name: /👤 Approve design/ })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Review the output of Modular monolith design, Cross-domain integration design")).toBeVisible();
  await approve.click();
  await expect(page.getByRole("heading", { name: /👤 Approve tests/ })).toBeVisible({ timeout: 30_000 });

  // The event this operation publishes is now a contract other migrations can reuse.
  await page.getByRole("link", { name: "Contracts", exact: true }).click();
  const row = page.getByRole("row", { name: new RegExp(`${operation} completed`) });
  await expect(row).toBeVisible();
  await expect(row.getByText("event", { exact: true })).toBeVisible();
  await row.getByRole("button", { name: new RegExp(`${operation} completed`) }).click();
  await expect(page.getByText("asyncapi: 3.0.0")).toBeVisible();

  // Loan's dependencies and the migration overview.
  await page.goto("/domains/loan?tab=dependencies");
  await expect(page.getByRole("heading", { name: "Loan depends on" })).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(`sync-api.*${operation}`) })).toBeVisible();
  await page.getByRole("link", { name: "Domains", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Cross-domain dependencies" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Suggested migration order" })).toBeVisible();

  // Ownership for the boundary check.
  await page.goto("/domains/loan?tab=boundary");
  const tables = `LOAN\nLOAN_${operation.toUpperCase()}`; // unique, so the form always has something to save
  await page.getByLabel("Owned tables").fill(tables);
  await page.getByRole("button", { name: "Save boundary" }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Owned tables")).toHaveValue(tables);
});
