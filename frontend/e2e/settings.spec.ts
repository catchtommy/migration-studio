import { expect, test } from "@playwright/test";

test.skip(!!process.env.E2E_STUB_QUESTIONS, "covered by the default run");

test("settings offer Copilot sign-in and corporate network options", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByLabel("Agents set to AI run with")).toHaveValue("copilot");
  const account = page.getByLabel("GitHub Copilot account");
  // The runtime starts (or downloads) on first use, which can take a while.
  await expect(account.getByText(/Not signed in to GitHub Copilot|Signed in to GitHub Copilot as/)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("link", { name: /Copilot: / })).toBeVisible();

  if (await account.getByRole("button", { name: "Sign in with GitHub" }).isVisible()) {
    await account.getByRole("button", { name: "Use a token instead" }).click();
    await account.getByLabel(/GitHub token for/).fill("ghp_classic_tokens_are_rejected");
    await account.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(account.getByText(/Classic personal access tokens/)).toBeVisible({ timeout: 60_000 });
  }

  // A GitHub Enterprise Cloud host: sign-in goes straight to it.
  await page.getByLabel("GitHub host").fill("acme.ghe.com");
  await page.getByRole("button", { name: "Save host" }).click();
  await expect(page.getByLabel("GitHub host")).toHaveValue("https://acme.ghe.com");
  if (await account.getByRole("button", { name: "Sign in with GitHub (acme.ghe.com)" }).isVisible()) {
    const popup = page.context().waitForEvent("page");
    await account.getByRole("button", { name: "Sign in with GitHub (acme.ghe.com)" }).click();
    await expect(account.getByRole("link", { name: "Open the GitHub sign-in page" })).toHaveAttribute("href", /^https:\/\/acme\.ghe\.com\/login\/oauth\/authorize/, {
      timeout: 60_000,
    });
    await (await popup).close();
    await account.getByRole("button", { name: "Cancel sign-in" }).click();
  }
  await page.getByLabel("GitHub host").fill("https://github.com");
  await page.getByRole("button", { name: "Save host" }).click();

  await page.getByLabel("HTTPS proxy").fill("");
  await expect(page.getByLabel("Copilot CLI path")).toBeVisible();
  await expect(page.getByText("Don't use WebSockets (for proxies that block them)")).toBeVisible();
});
