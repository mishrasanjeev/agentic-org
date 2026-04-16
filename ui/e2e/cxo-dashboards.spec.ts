/**
 * CxO Dashboards — E2E smoke suite.
 *
 * The six CxO dashboard pages (CEO/CFO/CHRO/CMO/COO/CBO) all render the
 * same KPI template in production today:
 *
 *   {Role} Dashboard [Demo Data]
 *   Agents | Total Tasks (30d) | Success Rate | HITL Interventions | Total Cost (USD)
 *   Total Tasks per Domain
 *   Domain Breakdown
 *   (empty state: "No agent activity yet. Once agents run tasks, KPIs will appear here.")
 *
 * This spec was previously a 58-test suite asserting legacy sections
 * (Revenue MTD, Recent Escalations, Agent Observatory, AR/AP Aging,
 * Monthly P&L, Bank Balances, Attrition Rate, etc.) that the shipped
 * product no longer renders. Those assertions were pure content drift
 * and were drowning CI with false positives.
 *
 * What we verify now, per dashboard:
 *   1. Page loads without a framework error.
 *   2. The dashboard title and Demo Data badge appear.
 *   3. The five shared KPI labels are visible.
 *   4. No "undefined" / "NaN" / "Cannot convert" text anywhere.
 *
 * When a richer dashboard template ships, extend this spec alongside the
 * component change rather than keeping stale assertions around.
 */
import { expect, test } from "@playwright/test";
import { APP, authenticate, canAuth } from "./helpers/auth";

const DASHBOARDS = [
  { title: "CEO Dashboard", path: "/dashboard/ceo" },
  { title: "CFO Dashboard", path: "/dashboard/cfo" },
  { title: "CHRO Dashboard", path: "/dashboard/chro" },
  { title: "CMO Dashboard", path: "/dashboard/cmo" },
  { title: "COO Dashboard", path: "/dashboard/coo" },
  { title: "CBO Dashboard", path: "/dashboard/cbo" },
];

const SHARED_KPIS = [
  "Agents",
  "Total Tasks (30d)",
  "Success Rate",
  "HITL Interventions",
  "Total Cost (USD)",
];

for (const { title, path } of DASHBOARDS) {
  test.describe(title, () => {
    test.beforeEach(async ({ page }) => {
      test.skip(!canAuth, "E2E_TOKEN required");
      await authenticate(page);
      await page.goto(`${APP}${path}`, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle").catch(() => {});
    });

    test(`loads without framework error`, async ({ page }) => {
      const body = (await page.locator("body").textContent()) || "";
      expect(body).not.toContain("Cannot GET");
      expect(body).not.toContain("Application error");
      await expect(page.getByText(title).first()).toBeVisible({ timeout: 15000 });
    });

    test(`shows the shared KPI template`, async ({ page }) => {
      for (const kpi of SHARED_KPIS) {
        await expect(page.getByText(kpi).first()).toBeVisible({
          timeout: 15000,
        });
      }
    });

    test(`no "undefined" or "NaN" rendered`, async ({ page }) => {
      // Give React one tick to render any async KPI values before asserting.
      await page.waitForTimeout(500);
      const body = (await page.locator("body").textContent()) || "";
      expect(body).not.toMatch(/\bundefined\b/);
      expect(body).not.toMatch(/\bNaN\b/);
      expect(body).not.toContain("Cannot convert");
    });
  });
}

// Cross-cutting: the role-switching nav exists in the sidebar and points to
// each sub-dashboard. This replaces the old "View Details quadrant link"
// test which asserted a CEO-dashboard layout the app no longer renders.
test.describe("CxO sidebar navigation", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
  });

  for (const { title, path } of DASHBOARDS) {
    test(`sidebar link for ${title}`, async ({ page }) => {
      const link = page.locator(`nav a[href="${path}"]`).first();
      await expect(link).toBeVisible({ timeout: 10000 });
    });
  }
});
