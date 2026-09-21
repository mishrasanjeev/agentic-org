// SPDX-License-Identifier: Apache-2.0
/**
 * Browser end-to-end suite for the local development stack (`make e2e`).
 *
 * Runs against the console served by docker-compose.dev.yml. BASE_URL is set
 * by the e2e compose service; there is deliberately no production default, so
 * this suite can never reach a hosted environment by accident.
 */
import { defineConfig } from "@playwright/test";

const baseURL = process.env.BASE_URL;
if (!baseURL) {
  throw new Error("BASE_URL is required: run this suite with `make e2e` against the dev stack");
}

export default defineConfig({
  testDir: ".",
  testMatch: ["dev-stack.spec.ts", "governed-cases*.spec.ts"],
  timeout: 60_000,
  retries: 0,
  workers: 1,
  outputDir: "../test-results/dev-stack",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "../playwright-report/dev-stack" }],
  ],
  use: {
    baseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [{ name: "dev-stack-chromium", use: { browserName: "chromium" } }],
});
