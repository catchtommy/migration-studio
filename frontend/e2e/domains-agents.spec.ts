import { expect, test } from "@playwright/test";
import { newMigration } from "./helpers";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "stub agents ask questions in this mode");

test("domain inputs and operation entry points reach the agents and show up in the domain's documentation", async ({ page }) => {
  const suffix = Date.now().toString(36);
  const name = `Collateral ${suffix}`;

  await page.goto("/domains");
  await page.getByRole("button", { name: "New domain" }).click();
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Description").fill("Collateral valuation and pledges");
  await page.getByRole("button", { name: "Add domain" }).click();

  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  const domainKey = new URL(page.url()).pathname.split("/").pop()!;
  // A new domain opens on its feature listing setup, not on a running listing.
  const listing = page.getByRole("region", { name: "Set up feature listing" });
  await expect(listing.getByRole("button", { name: "Start feature listing" })).toBeVisible();
  await expect(listing.getByRole("note")).toContainText("Stub");
  await listing.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("button", { name: "Agent inputs", exact: true }).click();
  await page.getByLabel("Shared context (all agents)").fill("Tables live in the COLL schema");
  const asIs = page.locator("section", { has: page.getByRole("heading", { name: "AS-IS documentation" }) });
  await asIs.getByLabel("Where to look").fill("src/main/java/com/acme/collateral/**\nsqlmap/Collateral.xml");
  await asIs.getByLabel("Notes").fill("Start at CollateralController");
  await page.getByRole("button", { name: "Save inputs" }).click();
  await expect(page.getByText("Saved")).toBeVisible();

  await newMigration(page, domainKey, "valuePledge");
  await page.getByRole("button", { name: "Add entry points & notes" }).click();
  await page.getByLabel("Entry points and files for this feature").fill("CollateralController.value");
  await page.getByRole("button", { name: "Start migration" }).click();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible({ timeout: 30_000 });

  await page.getByRole("link", { name: "Domains", exact: true }).click();
  await page.getByRole("link", { name: new RegExp(`${name}.*Collateral valuation and pledges`) }).click(); // the domain card, not the migration order
  await page.getByRole("button", { name: "Documentation", exact: true }).click();
  const doc = page.locator("article");
  await expect(doc.getByText("Tables live in the COLL schema")).toBeVisible();
  await expect(doc.getByText("sqlmap/Collateral.xml")).toBeVisible();
  await expect(doc.getByText("CollateralController.value")).toBeVisible();

  // A test domain with runs can be deleted with them, after typing its key.
  await page.getByRole("button", { name: "Details", exact: true }).click();
  page.once("dialog", (dialog) => dialog.accept(domainKey));
  await page.getByRole("button", { name: `Delete ${name} and its runs` }).click();
  await expect(page).toHaveURL(/\/domains$/);
  await expect(page.getByRole("link", { name: new RegExp(name) })).toHaveCount(0);
});

test("an agent's configuration can be edited and reset", async ({ page }) => {
  await page.goto("/agents/deep_dive");
  await page.getByRole("tab", { name: "Form" }).click(); // the form view is remembered from here on
  const instructions = page.getByLabel("Instructions");
  await expect(instructions).toHaveValue(/test scenario catalogue/);

  await instructions.fill("Focus on the nightly batch jobs.");
  await page.getByRole("radio", { name: "AI" }).click();
  await expect(page.getByText("Runs with GitHub Copilot, using your account")).toBeVisible();
  await page.getByRole("button", { name: "Save agent" }).click();
  await expect(page.getByText("Saved. Applies from the next stage run.")).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("Instructions")).toHaveValue("Focus on the nightly batch jobs.");
  await expect(page.getByRole("link", { name: /Deep dive/ })).toContainText("AI");

  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Reset to default" }).click();
  await expect(page.getByLabel("Instructions")).toHaveValue(/test scenario catalogue/);
  await expect(page.getByRole("link", { name: /Deep dive/ })).toContainText("Stub");
});
