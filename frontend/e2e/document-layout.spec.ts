import { expect, test } from "@playwright/test";
import { newMigration, PROJECT_HEADERS } from "./helpers";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "stub agents ask questions in this mode");

const WIDE = [
  "# Wide document",
  "",
  `A long path: src/main/java/com/acme/${"verylongpackagename/".repeat(12)}LoanApprovalController.java`,
  "",
  "| " + Array.from({ length: 14 }, (_, i) => `Column header ${i + 1}`).join(" | ") + " |",
  "|" + " --- |".repeat(14),
  "| " + Array.from({ length: 14 }, (_, i) => `value-${i + 1}-without-spaces-${"x".repeat(12)}`).join(" | ") + " |",
  "",
  "```java",
  `String sql = "SELECT ${Array.from({ length: 40 }, (_, i) => `column_${i}`).join(", ")} FROM LOAN_APPLICATION";`,
  "```",
].join("\n");

test("long tables, code and paths in a document stay inside its box", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const operation = `wide${Date.now()}`;
  await newMigration(page, "customer", operation);
  await page.getByRole("button", { name: "Start migration" }).click();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible({ timeout: 60_000 });
  const runId = page.url().split("/runs/")[1];
  const artifacts = await (await page.request.get(`/api/runs/${runId}/artifacts`, { headers: PROJECT_HEADERS })).json();
  const doc = artifacts.find((a: { stage: string; path: string }) => a.stage === "as_is_doc" && a.path.endsWith(".md"));
  await page.request.put(`/api/artifacts/${doc.id}`, { data: { content: WIDE }, headers: PROJECT_HEADERS });

  await page.reload();
  await page.getByRole("button", { name: /AS-IS documentation/ }).first().click();
  const article = page.locator("article").filter({ hasText: "Wide document" });
  await expect(article).toBeVisible();
  const sizes = await article.evaluate((el) => {
    const card = el.closest(".card") as HTMLElement;
    return { article: el.getBoundingClientRect().right, card: card.getBoundingClientRect().right, page: document.documentElement.scrollWidth, view: window.innerWidth };
  });
  expect(sizes.article).toBeLessThanOrEqual(sizes.card + 1); // the document stays inside its box
  expect(sizes.page).toBeLessThanOrEqual(sizes.view); // and the page doesn't scroll sideways
  await page.screenshot({ path: process.env.E2E_SHOT ?? "test-results/document-layout.png" });
});
