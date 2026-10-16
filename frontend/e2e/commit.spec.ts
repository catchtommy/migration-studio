import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

// Needs the backend on this machine: the test makes a git repository for it to clone and push to.
test.skip(!!process.env.E2E_REMOTE || !!process.env.E2E_STUB_QUESTIONS, "needs a local backend; stub questions get in the way");

const git = (cwd: string, ...args: string[]) => execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], { cwd, encoding: "utf8" }).trim();

test("a finished run's code is reviewed, its conflict resolved, and the feature branch pushed", async ({ page }) => {
  const repo = mkdtempSync(join(tmpdir(), "studio-e2e-monolith-"));
  git(repo, "init", "-q", "-b", "main");
  writeFileSync(join(repo, "App.java"), "class App {\n}\n");
  git(repo, "add", "-A");
  git(repo, "commit", "-qm", "initial");

  const key = `commit-${Date.now()}`;
  const project = { "X-Project": key };
  expect((await page.request.post("/api/projects", { data: { key, name: `Commit ${key}`, settings: { monolith_repo: repo } } })).ok()).toBeTruthy();
  await page.request.put("/api/account/git", { data: { name: "E2E Admin", email: "e2e@example.com" } });
  await page.request.post("/api/domains", { data: { key: "loan", name: "Loan", description: "" }, headers: project });
  await page.request.post("/api/domains/loan/features", { data: { key: "approve", name: "approve" }, headers: project });
  // The workflow's first agent step changes the monolith's code.
  const wf = await (await page.request.get("/api/workflows/standard", { headers: project })).json();
  const firstAgent = wf.steps.find((st: { kind: string }) => st.kind === "agent").key;
  const steps = wf.steps.map((st: { key: string }) => (st.key === firstAgent ? { ...st, code: { repository: "", base_branch: "" } } : st));
  await page.request.put("/api/workflows/standard?library=project", { data: { steps }, headers: project });

  // Start from the New migration form in that project: the feature branch can be renamed before starting.
  await page.goto("/");
  await page.getByRole("button", { name: "Switch project" }).click();
  await page.getByRole("option", { name: new RegExp(`Commit ${key}`) }).click();
  const featureBranch = page.getByLabel("Feature branch");
  await expect(featureBranch).toHaveValue("migration/loan/approve");
  await featureBranch.fill("feature/e2e-approve");
  await page.getByRole("button", { name: "Start migration" }).click();
  await expect(page.getByText("feature/e2e-approve").first()).toBeVisible();
  const runId = page.url().split("/runs/")[1];

  // Approve every gate.
  for (;;) {
    let run = await (await page.request.get(`/api/runs/${runId}`, { headers: project })).json();
    for (let i = 0; i < 300 && ["pending", "running"].includes(run.status); i++) {
      await page.waitForTimeout(100);
      run = await (await page.request.get(`/api/runs/${runId}`, { headers: project })).json();
    }
    if (run.status === "awaiting_branches") {
      await page.request.post(`/api/runs/${runId}/branches`, { data: { feature_branch: run.branch }, headers: project });
      continue;
    }
    if (run.status === "awaiting_commit" && run.current_stage) {
      await page.request.post(`/api/runs/${runId}/finish`, { headers: project }); // commit at the end instead
      continue;
    }
    if (run.status !== "awaiting_approval") break;
    await page.request.post(`/api/runs/${runId}/decision`, { data: { decision: "approve" }, headers: project });
  }

  // The agents' code change, and a change to the same lines on main meanwhile.
  const ws = (await (await page.request.get(`/api/runs/${runId}`, { headers: project })).json()).workspace;
  writeFileSync(join(ws, "App.java"), "class App {\n  void approveLoan() {}\n}\n");
  writeFileSync(join(ws, "LoanService.java"), "class LoanService {}\n");
  git(ws, "add", "-A");
  git(ws, "commit", "-qm", "agent work");
  writeFileSync(join(repo, "App.java"), "class App {\n  void hotfix() {}\n}\n");
  git(repo, "commit", "-qam", "hotfix");

  await page.reload();
  await expect(page.getByRole("heading", { name: "Review & commit" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("1 file conflict with main")).toBeVisible();
  const files = page.getByRole("list", { name: "Changed files" });
  await expect(files.getByLabel("Commit LoanService.java")).toBeChecked();
  await expect(files.getByRole("checkbox", { name: /\.migration\// }).first()).not.toBeChecked(); // documents start unticked

  await files.getByRole("button", { name: /App\.java/ }).click();
  await expect(page.getByText("1 conflict between this run and main.")).toBeVisible();
  await page.getByRole("button", { name: "Both (run's first)" }).click();
  await expect(page.getByText("Resolved: this is what will be committed.")).toBeVisible();

  await page.getByRole("button", { name: "Commit & push" }).click();
  await expect(page.getByText(/committed 3 files in Monolith as/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("feature/e2e-approve").first()).toBeVisible();

  expect(git(repo, "show", "feature/e2e-approve:App.java")).toBe("class App {\n  void approveLoan() {}\n  void hotfix() {}\n}");
  expect(git(repo, "log", "-1", "--format=%an", "feature/e2e-approve")).toBe("E2E Admin");
});

test("a step that changes another repository: set on the step, cut on the same branch, committed on its own tab", async ({ page }) => {
  const root = mkdtempSync(join(tmpdir(), "studio-e2e-repos-"));
  for (const name of ["monolith", "loan-service"]) {
    const repo = join(root, name);
    execFileSync("mkdir", [repo]);
    git(repo, "init", "-q", "-b", "main");
    writeFileSync(join(repo, "README.md"), `${name}\n`);
    git(repo, "add", "-A");
    git(repo, "commit", "-qm", "initial");
  }
  const key = `repos-${Date.now()}`;
  const project = { "X-Project": key };
  await page.request.post("/api/projects", { data: { key, name: `Repos ${key}`, settings: { monolith_repo: join(root, "monolith") } } });
  await page.request.put("/api/account/git", { data: { name: "E2E Admin", email: "e2e@example.com" } });
  await page.request.post("/api/domains", { data: { key: "loan", name: "Loan", description: "" }, headers: project });
  await page.request.post("/api/domains/loan/features", { data: { key: "approve", name: "approve" }, headers: project });

  await page.goto("/");
  await page.getByRole("button", { name: "Switch project" }).click();
  await page.getByRole("option", { name: new RegExp(`Repos ${key}`) }).click();

  // The second agent step changes each domain's own service repository.
  await page.goto("/workflows/standard");
  const code = page.getByRole("group", { name: "Step 2 code" });
  await code.getByLabel("Changes code").check();
  await code.getByLabel("In another repository").check();
  await code.getByLabel("Step 2 code repository").fill(join(root, "{domain}-service"));
  await page.getByText("This project only").click();
  await page.getByRole("button", { name: "Save workflow" }).click();
  await expect(page.getByText("Saved")).toBeVisible();

  await page.goto("/");
  await expect(page.getByLabel("Base branch of loan-service")).toBeVisible();
  await page.getByRole("button", { name: "Start migration" }).click();
  await expect(page.getByText(/also in loan-service/)).toBeVisible();
  const runId = page.url().split("/runs/")[1];
  for (;;) {
    let run = await (await page.request.get(`/api/runs/${runId}`, { headers: project })).json();
    for (let i = 0; i < 300 && ["pending", "running"].includes(run.status); i++) {
      await page.waitForTimeout(100);
      run = await (await page.request.get(`/api/runs/${runId}`, { headers: project })).json();
    }
    if (run.status === "awaiting_branches") {
      // The run asks before the step changes the service repository: confirm on the run page.
      await page.reload();
      await expect(page.getByRole("heading", { name: "Confirm branches for loan-service" })).toBeVisible();
      await expect(page.getByLabel("Feature branch")).toHaveValue("migration/loan/approve");
      await page.getByRole("button", { name: /Confirm and start/ }).click();
      await expect(page.getByRole("heading", { name: "Confirm branches for loan-service" })).toBeHidden({ timeout: 30_000 });
      continue;
    }
    if (run.status === "awaiting_commit" && run.current_stage) {
      // Right after the step changed the service, its changes are reviewed and committed on the run page.
      await page.reload();
      await expect(page.getByText(/changed code in loan-service/)).toBeVisible({ timeout: 30_000 });
      await expect(page.getByRole("list", { name: "Changed files" }).getByLabel(/Commit studio-stub\//)).toBeChecked();
      await page.getByLabel("Commit message").fill("Scaffold the loan service");
      await page.getByRole("button", { name: "Commit & push loan-service" }).click();
      await expect(page.getByText(/changed code in loan-service/)).toBeHidden({ timeout: 30_000 });
      continue;
    }
    if (run.status !== "awaiting_approval") break;
    await page.request.post(`/api/runs/${runId}/decision`, { data: { decision: "approve" }, headers: project });
  }

  const service = join(root, "loan-service");
  expect(git(service, "log", "-1", "--format=%s", "migration/loan/approve")).toBe("Scaffold the loan service");
  const done = await (await page.request.get(`/api/runs/${runId}`, { headers: project })).json();
  expect(done.status).toBe("completed"); // nothing else changed code
});
