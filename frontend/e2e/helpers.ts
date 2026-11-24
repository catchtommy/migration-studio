import { expect, type Page } from "@playwright/test";

/** Direct API calls from tests name the project the UI starts in (the browser's own requests send the app's). */
export const PROJECT_HEADERS = { "X-Project": process.env.E2E_PROJECT ?? "default" };

/** Adds a feature to a domain's list through the API (migrations can only start for listed features), then opens the
 * New migration form with the domain and feature chosen. */
export async function newMigration(page: Page, domain: string, feature: string, entryPoints: string[] = []) {
  const r = await page.request.post(`/api/domains/${domain}/features`, {
    data: { key: feature, name: feature, entry_points: entryPoints },
    headers: PROJECT_HEADERS,
  });
  if (!r.ok() && r.status() !== 409) throw new Error(`Adding feature ${feature} failed: ${r.status()} ${await r.text()}`);
  await page.goto(`/?domain=${domain}&feature=${feature}`);
}


/** Approves the gate the run waits at, as a reviewer does: "Approve…" opens the decision, every checklist item the gate
 * asks for is ticked, then "Approve". */
export async function approveGate(page: Page, timeout = 30_000) {
  const open = page.getByRole("button", { name: "Approve…" });
  await expect(open).toBeVisible({ timeout });
  await open.click();
  const checklist = page.getByRole("group", { name: "Checklist" }).getByRole("checkbox");
  for (const item of await checklist.all()) if (!(await item.isChecked())) await item.check();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
}

/** Rejects the gate the run waits at with what to change: re-run now, or hold the re-run to amend first. */
export async function rejectGate(page: Page, comments: string, choice: "now" | "amend" = "now", timeout = 30_000) {
  const open = page.getByRole("button", { name: "Reject…" });
  await expect(open).toBeVisible({ timeout });
  await open.click();
  await page.getByLabel(/What to change/).fill(comments);
  await page.getByRole("button", { name: choice === "now" ? "Reject & re-run now" : "Reject, amend first" }).click();
}
