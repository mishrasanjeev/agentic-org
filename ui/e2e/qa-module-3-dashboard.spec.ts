/**
 * Module 3: Dashboard — TC-DASH-001 through TC-DASH-005.
 *
 * The home dashboard is retired: `/dashboard` lands every signed-in role on
 * its landing page, Insights for the CxO, admin and auditor roles (the
 * E2E session's role). The Insights view composes from the Insights API,
 * which reads agents, approvals, runs and audit (each with its own module).
 * The tests here pin the page-level integration: `/dashboard` lands on
 * Insights, the tiles and the "Needs you" list render, and no runtime or
 * load error surfaces.
 */
import { expect, test } from "./helpers/test";

import { APP, authenticate, requireAuth } from "./helpers/auth";

test.describe("Module 3: Dashboard @qa @dashboard", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await authenticate(page);
  });

  // -------------------------------------------------------------------------
  // TC-DASH-001: Dashboard loads with metrics
  // -------------------------------------------------------------------------

  test("TC-DASH-001 /dashboard lands on Insights and renders its decision tiles", async ({
    page,
  }) => {
    await page.goto(`${APP}/dashboard`);
    await expect(page).toHaveURL(/\/dashboard\/insights/, { timeout: 15000 });
    await expect(page.getByRole("heading", { name: "Insights", exact: true })).toBeVisible();
    // The view's tiles are the landing metrics now.
    await expect(page.getByTestId("insights-tiles")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("decision-tile").first()).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // TC-DASH-002: Charts render
  // -------------------------------------------------------------------------

  test("TC-DASH-002 dashboard renders without runtime errors", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });

    await page.goto(`${APP}/dashboard`);
    await expect(page).toHaveURL(/\/dashboard\/insights/, { timeout: 15000 });
    // Wait for the tiles to settle.
    await page.getByTestId("insights-tiles").waitFor({
      state: "visible",
      timeout: 15000,
    });
    // Foundation #8: charts must not throw on null/empty data.
    // We don't pin the SVG count (chart internals), but we
    // do pin "no thrown errors" — that's what catches a chart
    // that NaN'd on a null value.
    expect(
      errors.filter(
        (e) =>
          // Filter out third-party noise (analytics, font loads,
          // ResizeObserver) that aren't dashboard regressions.
          !e.includes("ResizeObserver") &&
          !e.includes("Failed to load resource") &&
          !e.includes("favicon"),
      ),
      `unexpected page errors: ${errors.slice(0, 5).join(" | ")}`,
    ).toEqual([]);
  });

  // -------------------------------------------------------------------------
  // TC-DASH-003: Recent activity feed
  // -------------------------------------------------------------------------

  test("TC-DASH-003 Insights renders the Needs you list (with items or empty state)", async ({
    page,
  }) => {
    await page.goto(`${APP}/dashboard`);
    await expect(page).toHaveURL(/\/dashboard\/insights/, { timeout: 15000 });
    // Either the items OR the documented empty state must render.
    // Both prove the data path is wired; neither rendering would
    // mean the section was silently dropped.
    const needsYou = page.getByTestId("needs-you");
    await expect(needsYou).toBeVisible({ timeout: 15000 });
    await expect(needsYou.getByRole("heading", { name: "Needs you" })).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // TC-DASH-004: Pending approvals summary
  // -------------------------------------------------------------------------

  test("TC-DASH-004 approvals stay one click away from the Insights landing", async ({
    page,
  }) => {
    await page.goto(`${APP}/dashboard`);
    await expect(page).toHaveURL(/\/dashboard\/insights/, { timeout: 15000 });
    await expect(page.getByTestId("needs-you")).toBeVisible({ timeout: 15000 });
    // Items waiting for an approval link to the approvals pages, and the
    // sidebar links to /dashboard/approvals. We don't click it here (other
    // modules own that page), just confirm the link target.
    const link = page.locator('[href="/dashboard/approvals"]').first();
    await expect(link).toBeAttached();
  });

  // -------------------------------------------------------------------------
  // TC-DASH-005: CFO sees only finance data (RBAC inheritance)
  // -------------------------------------------------------------------------

  test("TC-DASH-005 Insights landing inherits RBAC scope from the Insights API", async ({
    page,
  }) => {
    // The E2E session has admin scope so every view comes back.
    // The per-role view restriction is enforced server-side by the
    // Insights API (403 for a view outside the role).
    // Here we just confirm that a logged-in user CAN load the
    // landing (proving the RBAC machinery doesn't 5xx for
    // ordinary tokens).
    await page.goto(`${APP}/dashboard`);
    await expect(page).toHaveURL(/\/dashboard\/insights/, { timeout: 15000 });
    await expect(page.getByTestId("insights-tiles")).toBeVisible({ timeout: 15000 });
    // No load or forbidden state (which would surface if the
    // Insights API 401'd, 403'd or 5xx'd for the test user).
    await expect(page.getByText("Could not load Insights.")).toHaveCount(0);
    await expect(page.getByText("No Insights view for your role")).toHaveCount(0);
  });
});
