import { writeFileSync } from "node:fs";
import { request, type FullConfig } from "@playwright/test";

export const AUTH_STATE = "e2e/.auth.json";

/** Signs in once for all tests (E2E_USERNAME / E2E_PASSWORD, an account with every permission). On a fresh stack with
 * no users yet, that account is created as the first admin. */
export default async function globalSetup(config: FullConfig) {
  const ctx = await request.newContext({ baseURL: config.projects[0].use.baseURL });
  const username = process.env.E2E_USERNAME ?? "e2e-admin";
  const password = process.env.E2E_PASSWORD ?? "e2e-admin-password";
  const me = await (await ctx.get("/api/auth/me")).json();
  const r = me.needs_bootstrap
    ? await ctx.post("/api/auth/bootstrap", { data: { username, display_name: "E2E Admin", password } })
    : await ctx.post("/api/auth/login", { data: { username, password } });
  if (!r.ok()) throw new Error(`Signing in for the e2e tests failed (${r.status()} ${await r.text()}); set E2E_USERNAME and E2E_PASSWORD to an admin account`);
  // Tests work in one project (E2E_PROJECT, default "default"), whichever other projects the stack has.
  const state = await ctx.storageState();
  const origin = new URL(config.projects[0].use.baseURL!).origin;
  state.origins = [{ origin, localStorage: [{ name: "migration-studio.project", value: process.env.E2E_PROJECT ?? "default" }] }];
  writeFileSync(AUTH_STATE, JSON.stringify(state, null, 2));
  await ctx.dispose();
}
