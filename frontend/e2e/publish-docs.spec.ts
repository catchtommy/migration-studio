import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

// Needs the backend on this machine: the test makes a git repository for it to push to.
test.skip(!!process.env.E2E_REMOTE || !!process.env.E2E_STUB_QUESTIONS, "needs a local backend; stub questions get in the way");

const git = (cwd: string, ...args: string[]) => execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd, encoding: "utf8" }).trim();

test("approved AS-IS documents are published to the monolith's docs folder from the run page", async ({ page }) => {
  const root = mkdtempSync(join(tmpdir(), "studio-e2e-docs-"));
  const work = join(root, "work");
  execFileSync("mkdir", [work]);
  git(work, "init", "-q", "-b", "main");
  writeFileSync(join(work, "App.java"), "class App {}\n");
  git(work, "add", "-A");
  git(work, "commit", "-qm", "initial");
  const bare = join(root, "monolith.git");
  git(root, "clone", "-q", "--bare", work, bare);

  const key = `docs-${Date.now()}`;
  const project = { "X-Project": key };
  await page.request.post("/api/projects", { data: { key, name: `Docs ${key}`, settings: { monolith_repo: bare } } });
  await page.request.put("/api/account/git", { data: { name: "E2E Admin", email: "e2e@example.com" } });
  await page.request.post("/api/domains", { data: { key: "loan", name: "Loan", description: "" }, headers: project });
  await page.request.post("/api/domains/loan/features", { data: { key: "approve", name: "approve" }, headers: project });
  const run = await (await page.request.post("/api/runs", { data: { domain: "loan", operation: "approve" }, headers: project })).json();

  await page.goto("/");
  await page.getByRole("button", { name: "Switch project" }).click();
  await page.getByRole("option", { name: new RegExp(`Docs ${key}`) }).click();
  await page.goto(`/runs/${run.id}`);
  await page.getByRole("button", { name: "Approve", exact: true }).click({ timeout: 60_000 });

  await expect(page.getByRole("heading", { name: "Publish AS-IS documentation to the monolith" })).toBeVisible({ timeout: 60_000 });
  const docs = page.getByRole("list", { name: "Documents to publish" });
  await expect(docs.getByLabel("Publish as_is_doc.md")).toBeChecked();
  await page.getByRole("button", { name: "Publish to main" }).click();
  await expect(page.getByText(/AS-IS documentation published to/)).toBeVisible({ timeout: 30_000 });

  expect(git(bare, "show", "main:docs/loan/approve/as_is_doc.md")).toContain("AS-IS documentation");
  expect(git(bare, "log", "-1", "--format=%an", "main")).toBe("E2E Admin");
});
