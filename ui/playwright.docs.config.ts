// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "documentation.spec.ts",
  timeout: 90_000,
  retries: 0,
  workers: 1,
  outputDir: "./test-results/docs",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "./playwright-report/docs" }],
  ],
  use: {
    baseURL: process.env.DOCS_BASE_URL || "http://127.0.0.1:4193",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: { browserName: "chromium", viewport: { width: 1440, height: 1000 } },
    },
    {
      name: "mobile",
      use: {
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
});
