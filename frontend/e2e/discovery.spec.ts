import { expect, test, type Page } from "@playwright/test";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "stub agents ask questions in this mode");

async function approveGate(page: Page, gate: string) {
  await expect(page.getByRole("heading", { name: new RegExp(`👤 ${gate}`) })).toBeVisible({ timeout: 30_000 });
  await page.getByLabel("Reviewer").fill("e2e-reviewer");
  const approve = page.getByRole("button", { name: "Approve", exact: true });
  await approve.click();
  await expect(approve).toBeHidden({ timeout: 30_000 }); // recorded when the decision is accepted
}

test("bounded contexts, then a domain's features, then migrating a feature", async ({ page }) => {
  // Step 1: the stub agent finds the known domains plus a "documents" context.
  await page.goto("/domains");
  const discovery = page.locator("section", { has: page.getByRole("heading", { name: "Discovery" }) });
  // Set it up first: the agent's mode is shown, and it can be pointed at existing documentation in the monolith.
  await discovery.getByRole("button", { name: /Find (bounded contexts|them again)…/ }).click();
  const setup = discovery.getByRole("region", { name: "Set up bounded contexts" });
  await expect(setup.getByText(/Bounded contexts.*agent is in Stub mode/)).toBeVisible();
  await setup.getByLabel("Where to look").fill("docs/architecture/*.md");
  await setup.getByRole("button", { name: "Start" }).click();
  await discovery.getByRole("link", { name: /Bounded contexts.*Open/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Whole monolith · bounded contexts");
  await page.getByText("Paths and notes given to the agents").click();
  await expect(page.getByText("docs/architecture/*.md").first()).toBeVisible();
  await approveGate(page, "Approve bounded contexts");

  await page.getByRole("link", { name: "Domains", exact: true }).click();
  await page.getByRole("link", { name: /Documents.*Everything about documents/ }).click();

  // Step 2: the domain opens on its feature list; list (or re-list) the features and approve them.
  await expect(page.getByRole("button", { name: "Features", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^(List|Re-list) features…$/ }).click();
  const listing = page.getByRole("region", { name: "Set up feature listing" });
  await listing.getByLabel("Where to look").fill("docs/documents/*.md");
  await listing.getByRole("button", { name: "Start feature listing" }).click();
  await page.getByRole("link", { name: /Feature listing.*Open/ }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("documents · feature list");
  await approveGate(page, "Approve feature list");

  await page.goto("/domains/documents");
  await expect(page.getByRole("row", { name: /Create documents/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /View documents/ })).toBeVisible();

  // Step 3: migrate a feature not migrated yet; the form arrives with it chosen and shows its entry points.
  // (A feature with a migration shows its status instead; add one by hand so the test can be repeated.)
  const feature = `e2e-${Date.now().toString(36)}`;
  await page.getByRole("button", { name: "Add by hand" }).click();
  await page.getByLabel("Name", { exact: true }).fill(`Archive ${feature}`);
  await page.getByLabel(/^Key/).fill(feature);
  await page.getByLabel(/^Entry points/).fill("ArchiveController.archive");
  await page.getByRole("button", { name: "Add feature" }).click();
  await page.getByRole("row", { name: new RegExp(`Archive ${feature}`) }).getByRole("link", { name: "Migrate" }).click();
  await expect(page.getByLabel("Feature")).toHaveValue(feature);
  await expect(page.getByText("ArchiveController.archive")).toBeVisible();
  await page.getByRole("button", { name: "Start migration" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(feature);
});
