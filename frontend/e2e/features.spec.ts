import { expect, test } from "@playwright/test";
import { PROJECT_HEADERS } from "./helpers";

test("a feature is edited on its own page, and the list comes back where it was", async ({ page }) => {
  const domain = `feat${Date.now()}`;
  await page.request.post("/api/domains", { data: { key: domain, name: `Feat ${domain}`, description: "" }, headers: PROJECT_HEADERS });
  for (let i = 1; i <= 30; i++) {
    const key = `f${String(i).padStart(2, "0")}`;
    await page.request.post(`/api/domains/${domain}/features`, { data: { key, name: `Feature ${key}` }, headers: PROJECT_HEADERS });
  }
  await page.goto(`/domains/${domain}?tab=features`);
  const row = page.locator("#feature-f28");
  await row.getByRole("link", { name: "Edit" }).click();

  await expect(page).toHaveURL(new RegExp(`/domains/${domain}/features/f28$`));
  await expect(page.getByRole("heading", { level: 1, name: "Feature f28" })).toBeVisible();
  await page.getByLabel("Description").fill("Edited on its own page");
  await page.getByRole("button", { name: "Save feature" }).click();

  await expect(page).toHaveURL(new RegExp(`/domains/${domain}\\?tab=features#feature-f28$`));
  await expect(row).toContainText("Edited on its own page");
  await expect(row).toBeInViewport(); // scrolled back to the row edited
});
