import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // Specs only: e2e/helpers holds vitest unit tests (*.test.ts) that
  // Playwright's default pattern would otherwise try to load.
  testMatch: "**/*.spec.ts",
  timeout: 60_000,
  retries: 2,
  // Explicit output dirs so the HTML reporter and test-artifacts dir
  // never clash (Playwright warns if `outputDir` sits inside
  // `reporter.outputFolder` or vice versa).
  outputDir: "./test-results",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "./playwright-report" }],
  ],
  use: {
    baseURL: process.env.BASE_URL || "https://app.agenticorg.ai",
    screenshot: "only-on-failure",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
