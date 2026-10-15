import { expect, test } from "@playwright/test";

test("an admin gives a user access to a project, and only then can they open it", async ({ page, browser }) => {
  const username = `access-${Date.now()}`;
  const created = await page.request.post("/api/users", {
    data: { username, display_name: `Access ${username}`, password: "password123", roles: [] },
  });
  expect(created.ok()).toBeTruthy();

  // Without project access they're signed in but can't open anything.
  const theirs = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const them = await theirs.newPage();
  await them.goto("/");
  await them.getByLabel("Username").fill(username);
  await them.getByLabel("Password").fill("password123");
  await them.getByRole("button", { name: "Sign in" }).click();
  await expect(them.getByText("You don't have access to any project yet")).toBeVisible();
  await them.getByRole("button", { name: new RegExp(`Access ${username}`) }).click();
  await expect(them.getByRole("button", { name: "Sign out" })).toBeVisible(); // not a dead end
  await them.keyboard.press("Escape");

  await page.goto("/users");
  const row = page.getByRole("listitem").filter({ hasText: `Access ${username}` }).first();
  await expect(row.getByText("no project access")).toBeVisible();
  await row.getByRole("button", { name: "Edit" }).click();
  await row.getByLabel("Access to Default project").selectOption("reviewer");
  await expect(row.getByText("Default project: Reviewer")).toBeVisible();

  await them.reload();
  await expect(them.getByRole("button", { name: "Switch project" })).toHaveText(/Default project/);
  await them.getByRole("button", { name: new RegExp(`Access ${username}`) }).click();
  await them.getByRole("button", { name: "Sign out" }).click();
  await expect(them.getByRole("button", { name: "Sign in" })).toBeVisible();
  await theirs.close();
});
