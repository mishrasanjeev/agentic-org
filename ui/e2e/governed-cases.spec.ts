// SPDX-License-Identifier: Apache-2.0
/**
 * Governed case queue and case detail against the local development stack
 * (`make dev && make seed && make seed-cases && make e2e`). Every response is
 * real: the console, its /api proxy, the API, the database and the memos the
 * reference agents wrote against the mock provider.
 */
import { expect, test } from "@playwright/test";
import {
  documentationScreenshot,
  expectNoAccessibilityViolations,
  expectNoHorizontalOverflow,
  missingPrerequisite,
  openCase,
  seededCase,
  signIn,
} from "./helpers/governed-cases";

test.describe("governed cases in the approvals console @dev-stack", () => {
  test.skip(() => missingPrerequisite() !== "", "governed case seed or sign-in password missing");

  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("the queue lists the cases awaiting a decision with their tier and recommendation", async ({
    page,
  }, testInfo) => {
    const clean = seededCase("gb-clean-brightwater");
    await page.goto("/dashboard/approvals");
    await page.getByRole("link", { name: "Governed cases" }).click();
    await expect(page).toHaveURL(/\/dashboard\/approvals\/cases$/);

    // Seeding submits a new case each run, so the row is found by its case reference.
    const row = page.getByTestId("governed-case-row").filter({ hasText: clean.case_ref });
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(row).toContainText("Awaiting decision");
    await expect(row).toContainText("risk");
    await expect(page.getByRole("button", { name: /^Awaiting decision/ })).toHaveAttribute("aria-pressed", "true");

    await documentationScreenshot(page, "governed-cases-queue");
    await expectNoAccessibilityViolations(page, testInfo);
  });

  test("a case opens from the queue and shows the memo, its citations and the policy score", async ({
    page,
  }, testInfo) => {
    const hit = seededCase("us-false-positive-oakhollow");
    await page.goto("/dashboard/approvals/cases");
    await page
      .getByTestId("governed-case-row")
      .filter({ hasText: hit.case_ref })
      .getByRole("link", { name: hit.legal_name })
      .click();
    await expect(page).toHaveURL(new RegExp(`/dashboard/approvals/cases/${hit.case_ref}$`));

    await expect(page.getByRole("heading", { level: 1, name: hit.legal_name })).toBeVisible();
    await expect(page.getByTestId("memo-recommendation")).not.toBeEmpty();

    // Every memo section the agent produced is on the page, each with a status.
    const sections = page.locator('[data-testid^="memo-section-"]');
    expect(await sections.count()).toBeGreaterThanOrEqual(5);

    // US-1: every section carries at least one evidence entry naming the
    // provider, the record and the field, and the citation links to that record.
    const screening = page.getByTestId("memo-section-screening");
    const firstCitation = screening.getByTestId("evidence-entry").first();
    await expect(firstCitation).toContainText("mock");
    await expect(firstCitation).toContainText("field");
    const recordHref = await firstCitation.locator('a[href^="#cited-record-"]').first().getAttribute("href");
    expect(recordHref).toBeTruthy();
    await expect(page.locator(recordHref as string)).toBeVisible();

    const policy = page.getByTestId("policy-score");
    await expect(policy).toContainText("business_onboarding_us");
    await expect(page.getByTestId("policy-example-warning")).toBeVisible();
    expect(await page.getByTestId("policy-rule").count()).toBeGreaterThan(0);

    await documentationScreenshot(page, "governed-case-memo");
    await expectNoAccessibilityViolations(page, testInfo);
  });

  test("a citation can be read back and is checked against the run's own calls", async ({ page }) => {
    const hit = seededCase("us-false-positive-oakhollow");
    await openCase(page, hit.case_ref);

    // Every cited record on a real case was returned by this run's provider calls.
    const records = page.getByTestId("record-traced");
    await expect(records.first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("citations-uncheckable")).toHaveCount(0);
    await expect(page.getByTestId("evidence-untraced")).toHaveCount(0);
    for (const note of await records.all()) {
      await expect(note).toContainText("This run fetched this record.");
    }

    // The passage behind a citation is fetched on request, re-hashed by the server, and shown as text.
    const show = page.getByTestId("show-passage").first();
    await expect(show).toBeVisible();
    await show.click();
    const passage = page.getByTestId("excerpt-passage").first();
    await expect(passage).toBeVisible({ timeout: 20_000 });
    await expect(passage).toContainText("re-hashed when it was read back");
    await expect(passage).not.toBeEmpty();
    await documentationScreenshot(page, "governed-case-citations");
  });

  test("a section the provider could not supply is shown as unchecked, not as a clear result", async ({ page }) => {
    const thin = seededCase("us-thin-file-brambleway");
    await openCase(page, thin.case_ref);
    const unavailable = page.getByTestId("section-not-available").first();
    await expect(unavailable).toBeVisible();
    await expect(unavailable).toContainText("treat it as missing evidence");
  });

  test("the case screens work at phone width without sideways scrolling", async ({ page }, testInfo) => {
    const clean = seededCase("gb-clean-brightwater");
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/dashboard/approvals/cases");
    await expect(page.getByTestId("governed-cases-table")).toBeVisible({ timeout: 20_000 });
    await expectNoHorizontalOverflow(page);
    await documentationScreenshot(page, "governed-cases-queue-mobile");

    await openCase(page, clean.case_ref);
    await expectNoHorizontalOverflow(page);
    await documentationScreenshot(page, "governed-case-memo-mobile");
    await expectNoAccessibilityViolations(page, testInfo);
  });
});
