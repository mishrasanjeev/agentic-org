// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { defineConfig } from "@playwright/test";

// The Insights journeys against the local production build with recorded API responses (never a live account):
// every role's landing view with a WCAG 2.2 AA scan, a drill from a tile to one run, an export in each format and
// a weekly scheduled report. Run in CI after the build, beside the text-encoding replay.
const suppliedURL = process.env.BASE_URL;
const url = new URL(suppliedURL || "http://127.0.0.1:4188");
if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname) || url.pathname !== "/") {
  throw new Error("The Insights journeys require a plain loopback HTTP origin");
}
const baseURL = url.origin;

export default defineConfig({
  testDir: "../tests/browser",
  testMatch: "insights.spec.ts",
  timeout: 60_000,
  retries: 0,
  workers: 1,
  outputDir: "../test-results/insights",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "../playwright-report/insights" }],
  ],
  use: { baseURL, browserName: "chromium", trace: "retain-on-failure", acceptDownloads: true },
  webServer: suppliedURL ? undefined : {
    command: "npm run preview -- --host 127.0.0.1 --port 4188 --strictPort",
    url: baseURL,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
