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

test("features move to a new domain through the review, with a table left behind", async ({ page }) => {
  const domain = `splitsrc${Date.now()}`;
  const target = `splitnew${Date.now()}`;
  await page.request.post("/api/domains", { data: { key: domain, name: `Split ${domain}`, description: "" }, headers: PROJECT_HEADERS });
  await page.request.put(`/api/domains/${domain}`, { data: { owned_tables: ["LOAN", "RATE"] }, headers: PROJECT_HEADERS });
  for (const [key, tables] of [["approve", ["LOAN", "RATE"]], ["close", ["LOAN"]]] as const) {
    await page.request.post(`/api/domains/${domain}/features`, { data: { key, name: `Feature ${key}`, tables }, headers: PROJECT_HEADERS });
  }
  await page.goto(`/domains/${domain}?tab=features`);
  await page.getByRole("checkbox", { name: "Select Feature approve to move" }).check();
  await page.getByRole("button", { name: "Move 1 selected…" }).click();
  await page.getByRole("radio", { name: "A new domain" }).check();
  await page.getByLabel(/^Key/).fill(target);
  await page.getByLabel("Name", { exact: true }).fill("Split target");

  const rate = page.getByRole("checkbox", { name: "Move table RATE" });
  await expect(rate).toBeChecked(); // only approve uses it
  await expect(page.getByRole("region", { name: "Tables" })).toContainText("close (stays)"); // LOAN is shared
  await rate.uncheck();
  await expect(page.getByRole("status").first()).toContainText("0 tables");
  await page.getByRole("checkbox", { name: "I have checked calls" }).check();
  await page.getByRole("button", { name: "Confirm move" }).click();

  await expect(page.getByText(/Moved 1 feature to/)).toContainText("Set its repositories");
  await expect(page.locator("#feature-approve")).toHaveCount(0);
  const moved = await (await page.request.get(`/api/domains/${target}/features`, { headers: PROJECT_HEADERS })).json();
  expect(moved.map((f: { key: string }) => f.key)).toEqual(["approve"]);
  expect(moved[0].moved_from).toEqual([domain]);
  const source = await (await page.request.get(`/api/domains/${domain}`, { headers: PROJECT_HEADERS })).json();
  expect(source.owned_tables).toEqual(["LOAN", "RATE"]); // nothing ticked left it
});
