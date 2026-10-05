import { expect, test } from "@playwright/test";
import { newMigration } from "./helpers";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "stub agents ask questions in this mode");

test("a rejection can be held while the inputs are amended, then re-run", async ({ page }) => {
  await newMigration(page, "customer", `amendOp${Date.now()}`);
  await page.getByRole("button", { name: "Start migration" }).click();

  await expect(page.getByRole("heading", { name: /👤 Approve analysis/ })).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Reviewer").fill("e2e-reviewer");
  await page.getByLabel(/Comments/).fill("Missing the nightly batch");
  await page.getByRole("button", { name: "Reject, amend first" }).click();

  const panel = page.getByRole("region", { name: "Amend before re-running" });
  await expect(panel).toBeVisible();
  await expect(page.getByText("Changes requested").first()).toBeVisible();
  await expect(panel.getByRole("link", { name: /Edit AS-IS documentation/ })).toBeVisible();
  await panel.getByLabel("Where to look (this run)").fill("BatchJob.java");
  await panel.getByLabel(/Comments for the agent/).fill("Missing the nightly batch: start at BatchJob.java");
  await panel.getByRole("button", { name: "Re-run AS-IS documentation" }).click();

  await expect(page.getByRole("button", { name: /×2/ }).first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByText("Operation inputs given to the agents").click();
  await expect(page.getByText("BatchJob.java").first()).toBeVisible();
});
