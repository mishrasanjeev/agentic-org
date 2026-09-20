// SPDX-License-Identifier: Apache-2.0
/**
 * Screening disposition review against the local development stack (PRD §3
 * US-3). The dispositions come from the Screening Disposition agent's run in
 * `make seed-cases`; a review is written once, so re-run `make seed-cases`
 * before re-running this suite.
 */
import { expect, test, type Locator, type Page } from "@playwright/test";
import {
  documentationScreenshot,
  expectNoAccessibilityViolations,
  missingPrerequisite,
  openCase,
  seededCase,
  signIn,
} from "./helpers/governed-cases";

async function unreviewedDisposition(page: Page): Promise<Locator> {
  const card = page
    .getByTestId("screening-disposition")
    .filter({ has: page.getByTestId("disposition-review-form") })
    .first();
  await expect(
    card,
    "no disposition on this case is awaiting review: run `make seed-cases` for fresh cases",
  ).toBeVisible({ timeout: 20_000 });
  return card;
}

test.describe("screening dispositions in the console @dev-stack", () => {
  test.skip(() => missingPrerequisite() !== "", "governed case seed or sign-in password missing");

  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("a hit arrives pre-analysed with a per-identifier comparison the analyst can accept", async ({
    page,
  }, testInfo) => {
    const withHit = seededCase("us-false-positive-oakhollow");
    await openCase(page, withHit.case_ref);

    const card = await unreviewedDisposition(page);
    const table = card.getByTestId("comparison-table");
    for (const identifier of ["Name", "Date of birth", "Nationality", "Address", "Associated entities"]) {
      await expect(table.getByRole("rowheader", { name: identifier })).toBeVisible();
    }
    await expect(card.getByTestId("proposed-outcome")).toContainText("Proposed:");
    await expect(card.getByTestId("evidence-entry").first()).toContainText("mock");

    await documentationScreenshot(page, "governed-case-dispositions");
    await expectNoAccessibilityViolations(page, testInfo);

    await card.getByRole("button", { name: "Record review" }).click();
    const review = card.getByTestId("disposition-review");
    await expect(review).toBeVisible({ timeout: 20_000 });
    await expect(review).toContainText("Accepted");
    // The analyst identity comes from the session, not from the page.
    await expect(review).toContainText("user:");
    await expect(card.getByTestId("disposition-review-form")).toHaveCount(0);
  });

  test("an override needs a written reason, and both are recorded", async ({ page }) => {
    const trueMatch = seededCase("gb-true-match-corvane");
    await openCase(page, trueMatch.case_ref);

    const card = await unreviewedDisposition(page);
    await card.getByRole("radio", { name: "Override it" }).click();
    await expect(card.getByRole("button", { name: "Record review" })).toBeDisabled();

    await card.getByLabel("Reason for the override (required)").fill(
      "Date of birth and nationality in the filing match the list entry.",
    );
    await card.getByRole("button", { name: "Record review" }).click();

    const review = card.getByTestId("disposition-review");
    await expect(review).toBeVisible({ timeout: 20_000 });
    await expect(review).toContainText("Overridden");
    await expect(review).toContainText("Date of birth and nationality in the filing match the list entry.");
    await documentationScreenshot(page, "governed-case-disposition-override");
  });

  test("a review is written once: the API refuses a second one", async ({ page }) => {
    const withHit = seededCase("us-false-positive-oakhollow");
    await openCase(page, withHit.case_ref);
    const reviewed = page.getByTestId("screening-disposition").filter({ has: page.getByTestId("disposition-review") });
    await expect(reviewed.first()).toBeVisible({ timeout: 20_000 });
    const hitId = await reviewed.first().getAttribute("data-hit-id");

    const cookies = await page.context().cookies();
    const csrf = cookies.find((c) => c.name === "agenticorg_csrf")?.value ?? "";
    const response = await page.request.post(
      `/api/v1/governed-cases/${withHit.case_ref}/screening-dispositions/${hitId}/review`,
      {
        headers: { "X-CSRF-Token": csrf, "content-type": "application/json" },
        data: { action: "accepted", final_outcome: "false_positive" },
      },
    );
    expect(response.status()).toBe(409);
    expect(await response.json()).toMatchObject({ error: { reason: "already_reviewed" } });
  });
});
