/**
 * CxO Dashboards — E2E smoke suite for the retired CxO dashboard URLs.
 *
 * The six CxO dashboard pages (CEO/CFO/CHRO/CMO/COO/CBO) are retired. Their
 * URLs stay valid so bookmarks and emailed links keep working:
 *
 *   /dashboard/ceo  -> /dashboard/insights?view=executive
 *   /dashboard/cfo  -> /dashboard/insights?view=finance
 *   /dashboard/chro -> /dashboard/insights?view=hr
 *   /dashboard/coo  -> /dashboard/insights?view=operations
 *   /dashboard/cbo  -> /dashboard/insights?view=back_office
 *   /dashboard/cmo  -> /dashboard/marketing-ops (Marketing operations)
 *
 * What we verify now, per legacy URL:
 *   1. The URL redirects to the page that holds its features, without a
 *      framework error.
 *   2. The new page renders: the Insights heading with the matching view
 *      selected, or the Marketing operations heading.
 *   3. The old KPI template (Demo Data badge, CMO Capability Status) is gone.
 *   4. No "undefined" / "NaN" / "Cannot convert" text anywhere.
 */
import { expect, test } from "./helpers/test";
import { APP, authenticate, canAuth, requireAuth } from "./helpers/auth";

const INSIGHTS_REDIRECTS = [
  { title: "CEO Dashboard", path: "/dashboard/ceo", view: "executive" },
  { title: "CFO Dashboard", path: "/dashboard/cfo", view: "finance" },
  { title: "CHRO Dashboard", path: "/dashboard/chro", view: "hr" },
  { title: "COO Dashboard", path: "/dashboard/coo", view: "operations" },
  { title: "CBO Dashboard", path: "/dashboard/cbo", view: "back_office" },
];

const RETIRED_PATHS = [...INSIGHTS_REDIRECTS.map((d) => d.path), "/dashboard/cmo"];

for (const { title, path, view } of INSIGHTS_REDIRECTS) {
  test.describe(title, () => {
    test.beforeEach(async ({ page }) => {
      requireAuth();
      await authenticate(page);
      await page.goto(`${APP}${path}`, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle").catch(() => {});
    });

    test(`redirects to the Insights ${view} view without framework error`, async ({ page }) => {
      await expect(page).toHaveURL(new RegExp(`/dashboard/insights\\?(.*&)?view=${view}(&|$)`), {
        timeout: 15000,
      });
      const body = (await page.locator("body").textContent()) || "";
      expect(body).not.toContain("Cannot GET");
      expect(body).not.toContain("Application error");
      await expect(page.getByRole("heading", { name: "Insights", exact: true })).toBeVisible({
        timeout: 15000,
      });
    });

    test(`shows the ${view} view selected, not the old KPI template`, async ({ page }) => {
      // The view is a selected tab, or the view's own heading when the role has a single view.
      const viewLabel = page.locator(`#insights-tab-${view}`);
      await expect(viewLabel).toBeVisible({ timeout: 15000 });
      if ((await viewLabel.getAttribute("role")) === "tab") {
        await expect(viewLabel).toHaveAttribute("aria-selected", "true");
      }
      await expect(page.getByText(title, { exact: true })).toHaveCount(0);
      await expect(page.getByText("Demo Data", { exact: true })).toHaveCount(0);
    });

    test(`no "undefined" or "NaN" rendered`, async ({ page }) => {
      // Give React one tick to render any async values before asserting.
      await page.waitForTimeout(500);
      const body = (await page.locator("body").textContent()) || "";
      expect(body).not.toMatch(/\bundefined\b/);
      expect(body).not.toMatch(/\bNaN\b/);
      expect(body).not.toContain("Cannot convert");
    });
  });
}

test.describe("CMO Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await authenticate(page);
    await page.goto(`${APP}/dashboard/cmo`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
  });

  test(`redirects to Marketing operations without framework error`, async ({ page }) => {
    await expect(page).toHaveURL(/\/dashboard\/marketing-ops(\?|$)/, { timeout: 15000 });
    const body = (await page.locator("body").textContent()) || "";
    expect(body).not.toContain("Cannot GET");
    expect(body).not.toContain("Application error");
    await expect(page.getByRole("heading", { name: "Marketing operations" }).first()).toBeVisible({
      timeout: 15000,
    });
  });

  test(`no longer shows the CMO KPI template`, async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Marketing operations" }).first()).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText("CMO Capability Status")).toHaveCount(0);
    await expect(page.getByText("Demo Data", { exact: true })).toHaveCount(0);
  });

  test(`no "undefined" or "NaN" rendered`, async ({ page }) => {
    await page.waitForTimeout(500);
    const body = (await page.locator("body").textContent()) || "";
    expect(body).not.toMatch(/\bundefined\b/);
    expect(body).not.toMatch(/\bNaN\b/);
    expect(body).not.toContain("Cannot convert");
  });
});

// Cross-cutting: the sidebar no longer lists the CxO dashboards; Insights and
// Marketing operations replace them.
test.describe("CxO sidebar navigation", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await authenticate(page);
    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
  });

  test("/dashboard lands on Insights", async ({ page }) => {
    await expect(page).toHaveURL(/\/dashboard\/insights/, { timeout: 15000 });
  });

  test("sidebar link for Insights", async ({ page }) => {
    const link = page.locator('nav a[href="/dashboard/insights"]').first();
    await expect(link).toBeVisible({ timeout: 10000 });
  });

  test("sidebar link for Marketing operations", async ({ page }) => {
    const link = page.locator('nav a[href="/dashboard/marketing-ops"]').first();
    await expect(link).toBeVisible({ timeout: 10000 });
  });

  for (const path of RETIRED_PATHS) {
    test(`no sidebar link for retired ${path}`, async ({ page }) => {
      await expect(page.locator('nav a[href="/dashboard/insights"]').first()).toBeVisible({ timeout: 10000 });
      await expect(page.locator(`nav a[href="${path}"]`)).toHaveCount(0);
    });
  }
});
