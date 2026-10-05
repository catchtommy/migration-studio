import { expect, test } from "@playwright/test";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "covered by the default run");

// An agent.md as kept in GitHub: no Migration Studio metadata.
const AGENT_MD = `---
name: security-reviewer
description: Reviews the extracted service for security issues
tools: ['read', 'search', 'web']
---

You review the microservice for OWASP Top 10 issues and write security-review.md.
`;

test("pasting a GitHub agent.md creates an agent, edited as text first", async ({ page }) => {
  const name = `Security reviewer ${Date.now().toString(36)}`;
  await page.goto("/agents");
  await page.getByRole("button", { name: "New agent" }).click();
  await page.getByLabel("agent.md text").fill(AGENT_MD.replace("name: security-reviewer", `name: ${name}`));
  await page.getByRole("button", { name: "Create agent" }).click();

  // The agent opens on its text, with the pasted header and instructions.
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByRole("tab", { name: "agent.md (free text)" })).toHaveAttribute("aria-selected", "true");
  const text = page.getByLabel("agent.md", { exact: true });
  await expect(text).toHaveValue(new RegExp(`^---\\nname: ${name}\\n[\\s\\S]*- read_file[\\s\\S]*OWASP Top 10`));

  // Paste a newer version from GitHub (no metadata): the note says which settings are kept.
  await text.fill(AGENT_MD.replace("name: security-reviewer", `name: ${name}`).replace("OWASP Top 10", "OWASP ASVS"));
  await expect(page.getByText(/no metadata section, so the agent keeps its current settings: Stub mode/)).toBeVisible();
  await page.getByRole("button", { name: "Save agent" }).click();
  await expect(page.getByText("Saved. Applies from the next stage run.")).toBeVisible();
  await expect(page.getByText(/Not available to Migration Studio agents.*web/)).toBeVisible();

  await page.reload();
  await expect(page.getByLabel("agent.md", { exact: true })).toHaveValue(/OWASP ASVS/);
  await page.getByRole("tab", { name: "Form" }).click();
  await expect(page.getByLabel("Instructions")).toHaveValue("You review the microservice for OWASP ASVS issues and write security-review.md.");
  await expect(page.getByRole("checkbox", { name: /^search_code/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /^write_file/ })).not.toBeChecked();

  // The chosen view is remembered.
  await page.reload();
  await expect(page.getByRole("tab", { name: "Form" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "agent.md (free text)" }).click();
});

test("agent.md files import, download, and a pasted agent.md in Instructions is applied", async ({ page }) => {
  const name = `Imported reviewer ${Date.now().toString(36)}`;
  await page.goto("/agents");
  await page.getByRole("button", { name: "New agent" }).click();
  await page.getByLabel("Import from agent.md").setInputFiles({
    name: "security-reviewer.agent.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(AGENT_MD.replace("name: security-reviewer", `name: ${name}`)),
  });
  await expect(page.getByText("Imported security-reviewer.agent.md")).toBeVisible();
  await page.getByRole("button", { name: "Create agent" }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();

  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download agent.md" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.agent\.md$/);

  // A whole agent.md pasted into the form's Instructions box is recognised and applied.
  await page.getByRole("tab", { name: "Form" }).click();
  await page.getByLabel("Instructions").fill(AGENT_MD);
  await page.getByRole("button", { name: "Apply agent.md" }).click();
  await expect(page.getByLabel("Instructions")).toHaveValue("You review the microservice for OWASP Top 10 issues and write security-review.md.");
  await expect(page.getByLabel("Title").first()).toHaveValue("Security reviewer");
  await page.getByRole("tab", { name: "agent.md (free text)" }).click(); // leave the default view for other tests
});
