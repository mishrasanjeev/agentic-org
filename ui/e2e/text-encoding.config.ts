// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { defineConfig } from "@playwright/test";
import { textEncodingOrigin } from "./helpers/textEncodingOrigin";

const suppliedURL = process.env.BASE_URL;
const baseURL = textEncodingOrigin(suppliedURL);

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
  use: { baseURL, browserName: "chromium", trace: "retain-on-failure" },
  webServer: suppliedURL ? undefined : {
    command: "npm run preview -- --host 127.0.0.1 --port 4187 --strictPort",
    url: baseURL,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
