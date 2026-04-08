/**
 * Regression Suite Configuration
 *
 * This config runs ALL E2E tests as a regression suite against production.
 * Every spec file in e2e/ is included. Every page, every feature, every
 * CxO dashboard is tested.
 *
 * Usage:
 *   E2E_TOKEN=<token> BASE_URL=https://app.agenticorg.ai npx playwright test --config=e2e/regression.config.ts
 *
 * Spec files included (20 files, 438+ tests):
 *   - ca-firms.spec.ts          (58 tests) — CA flow, companies, approvals, credentials
 *   - cxo-dashboards.spec.ts    (45 tests) — CEO/CFO/CHRO/CMO/COO/CBO dashboards
 *   - production-full.spec.ts   (~80 tests) — All dashboard pages, agent/workflow CRUD
 *   - onboarding-e2e.spec.ts    (~60 tests) — Signup, first login, agent creation
 *   - v4-features.spec.ts       (~50 tests) — A2A, MCP, Grantex, packs, voice, RAG
 *   - qa-bugs-regression.spec.ts(~100 tests) — Historical bug fix regression
 *   - security-tests.spec.ts    — Auth, RBAC, CSP, injection tests
 *   - negative-cases.spec.ts    — Error handling, invalid inputs
 *   - error-handling.spec.ts    — 4xx/5xx responses, graceful degradation
 *   - app-routes.spec.ts        — All routes return 200
 *   - landing.spec.ts           — Landing page sections
 *   - login-e2e.spec.ts         — Login flow, demo accounts
 *   - scope-enforcement.spec.ts — RBAC scope enforcement
 *   - flows.spec.ts             — Multi-step user flows
 *   - full-app.spec.ts          — Full app smoke test
 *   - cfo-demo.spec.ts          — CFO demo walkthrough
 *   - integration-production.spec.ts — Production integration
 *   - evals-thorough.spec.ts    — Evaluation matrix
 *   - sop-flow.spec.ts          — SOP workflow
 *   - video-recordings.spec.ts  — Video recording flows
 */

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  timeout: 60_000,
  retries: 2,
  workers: 1, // Sequential for regression stability
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "../test-results/regression-report" }],
    ["json", { outputFile: "../test-results/regression-results.json" }],
  ],
  use: {
    baseURL: process.env.BASE_URL || "https://app.agenticorg.ai",
    screenshot: "only-on-failure",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "regression-chromium",
      use: { browserName: "chromium" },
    },
  ],
});
