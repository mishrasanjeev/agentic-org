// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * The merchant onboarding desk end-to-end suite (`make e2e-onboarding-desk`).
 *
 * It runs in the decision-grant runner, for the same reason as that suite: the
 * approval is taken on the Grantex auth service's own page, which answers only
 * on the loopback origin the service publishes. The runner shares that
 * service's network namespace, which is also where the stack's worker delivers
 * the governed-case push to this suite's receiver.
 */
import { defineConfig } from "@playwright/test";

const baseURL = process.env.BASE_URL;
if (!baseURL) {
  throw new Error("BASE_URL is required: run this suite with `make e2e-onboarding-desk` against the dev stack");
}
for (const name of ["GRANTEX_APPROVAL_ORIGIN", "AGENTICORG_SEED_PASSWORD", "ONBOARDING_DESK_SEED", "ONBOARDING_DESK_RUN"]) {
  if (!process.env[name]) throw new Error(`${name} is required: run this suite with \`make e2e-onboarding-desk\``);
}

export default defineConfig({
  testDir: ".",
  testMatch: ["onboarding-desk.spec.ts"],
  timeout: 240_000,
  retries: 0,
  workers: 1,
  outputDir: "../test-results/onboarding-desk",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "../playwright-report/onboarding-desk" }],
  ],
  use: {
    baseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [{ name: "onboarding-desk-chromium", use: { browserName: "chromium" } }],
});
