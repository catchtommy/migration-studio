import type { Page } from "@playwright/test";

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
