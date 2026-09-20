// SPDX-License-Identifier: Apache-2.0
/**
 * Helpers for the governed case browser suite against the local development
 * stack: the seeded sign-in, the cases `make seed-cases` created, an
 * accessibility scan and the screenshots the console documentation uses.
 */
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, type TestInfo } from "@playwright/test";

export interface SeededCase {
  case_ref: string;
  legal_name: string;
  state: string;
  tier?: string;
  recommendation?: string;
  screening_hits?: number;
  dispositions_proposed?: number;
  disposition_outcomes?: string[];
}

export interface SeededCases {
  tenant_id: string;
  cases: Record<string, SeededCase>;
}

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const SEED_PATH = path.resolve(REPO_ROOT, process.env.GOVERNED_CASES_SEED || "ui/test-results/governed-cases-seed.json");
export const SCREENSHOT_DIR = path.resolve(REPO_ROOT, "docs/console/images");

export const SEED_PASSWORD = process.env.AGENTICORG_SEED_PASSWORD || "";
export const APPROVER_A = "approver.a@example.com";
export const APPROVER_B = "approver.b@example.com";

/** The cases `make seed-cases` created, or null when the seed has not been run. */
export function seededCases(): SeededCases | null {
  if (!existsSync(SEED_PATH)) return null;
  const parsed = JSON.parse(readFileSync(SEED_PATH, "utf-8")) as SeededCases;
  return parsed.cases && Object.keys(parsed.cases).length > 0 ? parsed : null;
}

/** Why the suite cannot run, or "" when it can. Never silently passes. */
export function missingPrerequisite(): string {
  if (!SEED_PASSWORD) return "AGENTICORG_SEED_PASSWORD is not set: run `AGENTICORG_SEED_PASSWORD=... make seed`";
  if (!seededCases()) return `no seeded governed cases at ${SEED_PATH}: run \`make seed-cases\``;
  return "";
}

export function seededCase(key: string): SeededCase {
  const seed = seededCases();
  if (!seed) throw new Error("the governed case seed is missing");
  const found = seed.cases[key];
  if (!found) throw new Error(`the seed has no case for fixture ${key}`);
  return found;
}

export async function signIn(page: Page, email = APPROVER_A): Promise<void> {
  await page.goto("/login");
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', SEED_PASSWORD);
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/api/v1/auth/login") && r.request().method() === "POST"),
    page.locator('button[type="submit"]').click(),
  ]);
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

export async function openCase(page: Page, caseRef: string): Promise<void> {
  await page.goto(`/dashboard/approvals/cases/${caseRef}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 20_000 });
}

/** Fails the test on any accessibility violation on the screens under test. */
export async function expectNoAccessibilityViolations(page: Page, testInfo: TestInfo): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  await testInfo.attach("axe-violations.json", {
    body: JSON.stringify(results.violations, null, 2),
    contentType: "application/json",
  });
  expect(
    results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
    "axe accessibility violations",
  ).toEqual([]);
}

/** A documentation screenshot; `make e2e` regenerates every one of them. */
export async function documentationScreenshot(page: Page, name: string): Promise<void> {
  mkdirSync(SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, `${name}.png`), fullPage: true });
}

/** The page must not scroll sideways at the width under test. */
export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow, "horizontal overflow in pixels").toBeLessThanOrEqual(1);
}
