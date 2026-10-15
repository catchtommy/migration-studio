import { expect, test } from "@playwright/test";
import { newMigration } from "./helpers";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "stub agents ask questions in this mode");

test("a workflow built in the UI drives a run, including its approval gate", async ({ page }) => {
  const name = `Docs only ${Date.now().toString(36)}`;

  await page.goto("/workflows");
  await page.getByRole("button", { name: "New workflow" }).click();
  await page.getByLabel("Name").first().fill(name);
  await page.getByLabel("Start from").selectOption({ label: "A single agent step" });
  await page.getByRole("button", { name: "Create workflow" }).click();

  await expect(page.getByRole("link", { name: new RegExp(name) })).toBeVisible();
  await expect(page.getByLabel("Step 1 agent")).toHaveValue("as_is_doc");
  await page.getByRole("button", { name: "+ Add approval gate" }).click();
  await page.getByLabel("Step 2 gate title").fill("Approve docs");
  await expect(page.getByRole("checkbox", { name: "AS-IS documentation" })).toBeChecked();
  await page.getByRole("button", { name: "+ Add agent step" }).click();
  await expect(page.getByLabel("Step 3 agent")).toHaveValue("deep_dive");
  const inputs = page.getByRole("group", { name: "Step 3 inputs" });
  await inputs.getByRole("radio", { name: "Selected steps" }).check();
  await expect(inputs.getByRole("checkbox", { name: "AS-IS documentation" })).toBeChecked();
  await inputs.getByRole("checkbox", { name: "Include contents in prompt" }).check();
  await page.getByRole("button", { name: "Save workflow" }).click();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();

  await newMigration(page, "broker", `wfOp${Date.now()}`);
  await page.getByLabel("Workflow").selectOption({ label: name });
  await page.getByRole("button", { name: "Start migration" }).click();

  await expect(page.getByText(`${name} workflow`)).toBeVisible();
  const timeline = page.locator("aside li");
  await expect(timeline).toHaveCount(3);
  await expect(page.getByRole("heading", { name: "👤 Approve docs" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByText("Completed").first()).toBeVisible({ timeout: 30_000 });

  // The deep dive received the AS-IS document, with its contents.
  await page.locator("aside").getByRole("button", { name: /Deep dive/ }).click();
  await expect(page.getByText("Uses output of: AS-IS documentation (contents included in its prompt)")).toBeVisible();
  await expect(page.locator("article").getByText(/as_is_doc\.md.*\(contents included\)/)).toBeVisible();
});
