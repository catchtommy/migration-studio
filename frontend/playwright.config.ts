import { defineConfig } from "@playwright/test";

// Expects the backend (:8000, stub agents) and `npm run dev` (:5173) to be running; E2E_BASE_URL points elsewhere.
// questions.spec.ts also needs the backend started with STUDIO_STUB_QUESTIONS=1 and E2E_STUB_QUESTIONS=1 set here.
export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts", // signs in; see the file for the account used
  timeout: 120_000,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173", trace: "retain-on-failure", storageState: "e2e/.auth.json" },
});
