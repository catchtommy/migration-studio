import { expect, test } from "@playwright/test";

test.skip(!process.env.E2E_STUB_QUESTIONS, "needs a backend started with STUDIO_STUB_QUESTIONS=1");

test("the run pauses for an agent's question and continues once it is answered", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Domain").selectOption("loan");
  await page.getByLabel("Operation").fill(`askOp${Date.now()}`);
  await page.getByRole("button", { name: "Start migration" }).click();

  const panel = page.getByRole("region", { name: "Agent question" });
  await expect(panel.getByRole("heading", { name: "The AS-IS documentation agent has a question" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Needs input").first()).toBeVisible();
  await panel.getByLabel("Your name").fill("e2e-reviewer");
  await panel.getByRole("button", { name: "See the notes on the domain" }).click();
  await panel.getByRole("button", { name: "Send answer" }).click();

  await expect(panel.getByRole("heading", { name: "The Deep dive agent has a question" })).toBeVisible({ timeout: 30_000 });
  await panel.getByRole("button", { name: "Skip" }).click();

  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: /AS-IS documentation/ }).click();
  await expect(page.locator("article").getByText("See the notes on the domain")).toBeVisible();
  await page.getByText("1 question answered during this attempt").click();
  await expect(page.getByText("— e2e-reviewer")).toBeVisible();
});
