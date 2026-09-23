// SPDX-License-Identifier: Apache-2.0
/**
 * The decision-grant end-to-end suite (`make e2e-decisions`).
 *
 * It is separate from `dev-stack.config.ts` because it needs more than the
 * console: it drives the Grantex auth service's own approval page, which only
 * answers on the loopback origin the service publishes. The runner therefore
 * shares the auth service's network namespace (the `e2e-decisions` compose
 * service), and both origins are required here with no defaults, so this
 * suite can never reach a hosted environment by accident.
 */
import { defineConfig } from "@playwright/test";

const baseURL = process.env.BASE_URL;
if (!baseURL) {
  throw new Error("BASE_URL is required: run this suite with `make e2e-decisions` against the dev stack");
}
if (!process.env.GRANTEX_APPROVAL_ORIGIN) {
  throw new Error("GRANTEX_APPROVAL_ORIGIN is required: the auth service's own origin, where approvals happen");
}
if (!process.env.AGENTICORG_SEED_PASSWORD) {
  throw new Error("AGENTICORG_SEED_PASSWORD is required: seed and run the local decision-grant suite with the same password");
}

export default defineConfig({
  testDir: ".",
  testMatch: ["decision-grants.spec.ts"],
  // A four-eyes decision is two browser sign-ins, two approvals and a
  // server-measured dwell floor between rendering and submitting each one.
  timeout: 180_000,
  retries: 0,
  workers: 1,
  outputDir: "../test-results/decision-grants",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "../playwright-report/decision-grants" }],
  ],
  use: {
    baseURL,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [{ name: "decision-grants-chromium", use: { browserName: "chromium" } }],
});
