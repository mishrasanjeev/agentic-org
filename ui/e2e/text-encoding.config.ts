// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { defineConfig } from "@playwright/test";

const suppliedURL = process.env.BASE_URL;
const baseURL = new URL(suppliedURL || "http://127.0.0.1:4187");
if (
  baseURL.protocol !== "http:" ||
  !["localhost", "127.0.0.1"].includes(baseURL.hostname) ||
  baseURL.username || baseURL.password ||
  baseURL.pathname !== "/" || baseURL.search || baseURL.hash
) {
  throw new Error("Text encoding fixtures require a plain loopback HTTP origin");
}

export default defineConfig({
  testDir: "../tests/browser",
  testMatch: "text-encoding.spec.ts",
  timeout: 60_000,
  retries: 0,
  workers: 1,
  outputDir: "../test-results/text-encoding",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "../playwright-report/text-encoding" }],
  ],
  use: { baseURL: baseURL.origin, browserName: "chromium", trace: "retain-on-failure" },
  webServer: suppliedURL ? undefined : {
    command: "npm run preview -- --host 127.0.0.1 --port 4187 --strictPort",
    url: baseURL.origin,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
