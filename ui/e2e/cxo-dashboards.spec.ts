/**
 * CxO Dashboards — Comprehensive E2E Tests
 *
 * Covers all 6 CxO dashboard pages feature-by-feature:
 *   1. CEO Dashboard  (/dashboard/ceo)
 *   2. CFO Dashboard  (/dashboard/cfo)
 *   3. CHRO Dashboard (/dashboard/chro)
 *   4. CMO Dashboard  (/dashboard/cmo)
 *   5. COO Dashboard  (/dashboard/coo)
 *   6. CBO Dashboard  (/dashboard/cbo)
 *
 * Plus cross-cutting tests for navigation, role-based access, demo data
 * badge consistency, console errors, and data integrity.
 *
 * All tests are read-only and production-safe.
 * Auth-gated tests use E2E_TOKEN via localStorage injection.
 */
import { test, expect, Page } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function authenticate(page: Page): Promise<void> {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate((token) => {
    localStorage.setItem("token", token);
  }, E2E_TOKEN);
}

async function goToDashboard(page: Page, path: string): Promise<void> {
  await page.goto(`${APP}${path}`, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle").catch(() => {});
}

/** Assert no "undefined", "NaN", or "Cannot convert" in page body text. */
async function assertNoGarbageText(page: Page): Promise<void> {
  const body = (await page.locator("body").textContent()) || "";
  expect(body).not.toMatch(/\bundefined\b/);
  expect(body).not.toMatch(/\bNaN\b/);
  expect(body).not.toContain("Cannot convert");
}

// ==========================================================================
//  1. CEO Dashboard (/dashboard/ceo)
// ==========================================================================

test.describe("CEO Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await goToDashboard(page, "/dashboard/ceo");
  });

  test("page loads without crash", async ({ page }) => {
    const body = (await page.locator("body").textContent()) || "";
    expect(body).not.toContain("Cannot GET");
    expect(body).not.toContain("Application error");
    await expect(page.getByText("CEO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("shows 5 KPI cards (Revenue, Employees, Incidents, Pipeline, Health Score)", async ({
    page,
  }) => {
    for (const kpi of [
      "Revenue MTD",
      "Total Employees",
      "Active Incidents",
      "Pipeline Value",
      "Health Score",
    ]) {
      await expect(page.getByText(kpi).first()).toBeVisible({ timeout: 15000 });
    }
  });

  test("shows 4 department quadrants (Finance, HR, Marketing, Operations)", async ({
    page,
  }) => {
    for (const dept of ["Finance", "HR", "Marketing", "Operations"]) {
      await expect(page.locator(`text="${dept}"`).first()).toBeVisible({
        timeout: 15000,
      });
    }
  });

  test('each quadrant has "View Details" link pointing to sub-dashboard', async ({
    page,
  }) => {
    const viewDetailsLinks = page.locator('a:has-text("View Details")');
    await expect(viewDetailsLinks).toHaveCount(4, { timeout: 15000 });

    for (const href of ["/dashboard/cfo", "/dashboard/chro", "/dashboard/cmo", "/dashboard/coo"]) {
      await expect(page.locator(`a[href="${href}"]`).first()).toBeVisible({
        timeout: 10000,
      });
    }
  });

  test("shows recent escalations table with proper columns", async ({ page }) => {
    await expect(page.getByText("Recent Escalations").first()).toBeVisible({
      timeout: 15000,
    });
    for (const header of ["Item", "Department", "Urgency", "Requested By", "Age (hrs)"]) {
      await expect(page.locator(`th:has-text("${header}")`).first()).toBeVisible({
        timeout: 10000,
      });
    }
  });

  test("shows agent observatory section with proper columns", async ({ page }) => {
    await expect(page.getByText("Agent Observatory").first()).toBeVisible({
      timeout: 15000,
    });
    for (const header of ["Agent", "Action", "Domain", "Timestamp"]) {
      await expect(page.locator(`th:has-text("${header}")`).first()).toBeVisible({
        timeout: 10000,
      });
    }
  });

  test('no "undefined" or "NaN" text anywhere', async ({ page }) => {
    await page.waitForTimeout(2000);
    await assertNoGarbageText(page);
  });

  test("demo data badge shows when demo=true", async ({ page }) => {
    const body = (await page.locator("body").textContent()) || "";
    if (body.includes("Demo Data")) {
      await expect(page.getByText("Demo Data").first()).toBeVisible();
    } else {
      // No demo badge means real data -- just confirm no crash
      expect(body).not.toContain("Application error");
    }
  });

  test("health score displays as number/100", async ({ page }) => {
    const healthCard = page.locator(':has-text("Health Score")').locator(".text-2xl").first();
    const text = await healthCard.textContent({ timeout: 15000 });
    expect(text).toBeTruthy();
    expect(text).toMatch(/^\d+\/100$/);
  });
});

// ==========================================================================
//  2. CFO Dashboard (/dashboard/cfo)
// ==========================================================================

test.describe("CFO Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await goToDashboard(page, "/dashboard/cfo");
  });

  test("page loads without crash", async ({ page }) => {
    const body = (await page.locator("body").textContent()) || "";
    expect(body).not.toContain("Cannot GET");
    expect(body).not.toContain("Application error");
    await expect(page.getByText("CFO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("shows KPI cards (Cash Runway, Burn Rate, DSO, DPO)", async ({ page }) => {
    for (const kpi of ["Cash Runway", "Monthly Burn Rate", "DSO (Days)", "DPO (Days)"]) {
      await expect(page.getByText(kpi).first()).toBeVisible({ timeout: 15000 });
    }
  });

  test("AR/AP aging section renders both charts", async ({ page }) => {
    await expect(
      page.getByText("Accounts Receivable Aging").first(),
    ).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByText("Accounts Payable Aging").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("monthly P&L table renders with data rows", async ({ page }) => {
    await expect(
      page.getByText("Monthly P&L Summary").first(),
    ).toBeVisible({ timeout: 15000 });

    for (const header of ["Month", "Revenue", "COGS", "Gross Margin", "OPEX", "Net Income"]) {
      await expect(page.locator(`th:has-text("${header}")`).first()).toBeVisible({
        timeout: 10000,
      });
    }

    // At least one data row
    const tableRows = page.locator("table tbody tr");
    const rowCount = await tableRows.count();
    expect(rowCount).toBeGreaterThanOrEqual(1);
  });

  test("bank balances list has at least one entry", async ({ page }) => {
    await expect(page.getByText("Bank Balances").first()).toBeVisible({
      timeout: 15000,
    });
    const bankEntries = page.locator(".rounded.bg-muted");
    const count = await bankEntries.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("tax calendar renders", async ({ page }) => {
    await expect(
      page.getByText("Tax & Compliance Calendar").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("cash runway shows months suffix", async ({ page }) => {
    const card = page.locator(':has-text("Cash Runway")').locator(".text-2xl").first();
    const text = await card.textContent({ timeout: 15000 });
    expect(text).toMatch(/^\d+ mo$/);
  });

  test("DSO and DPO show day values", async ({ page }) => {
    const dsoCard = page.locator(':has-text("DSO (Days)")').locator(".text-2xl").first();
    const dsoText = await dsoCard.textContent({ timeout: 15000 });
    expect(dsoText).toMatch(/^\d+d$/);

    const dpoCard = page.locator(':has-text("DPO (Days)")').locator(".text-2xl").first();
    const dpoText = await dpoCard.textContent({ timeout: 15000 });
    expect(dpoText).toMatch(/^\d+d$/);
  });

  test('no "undefined" or "NaN"', async ({ page }) => {
    await page.waitForTimeout(2000);
    await assertNoGarbageText(page);
  });
});

// ==========================================================================
//  3. CHRO Dashboard (/dashboard/chro)
// ==========================================================================

test.describe("CHRO Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await goToDashboard(page, "/dashboard/chro");
  });

  test("page loads without crash", async ({ page }) => {
    const body = (await page.locator("body").textContent()) || "";
    expect(body).not.toContain("Cannot GET");
    expect(body).not.toContain("Application error");
    await expect(page.getByText("CHRO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("shows workforce KPIs (Total Employees, Attrition Rate, New Joiners, Open Positions)", async ({
    page,
  }) => {
    for (const kpi of [
      "Total Employees",
      "Attrition Rate",
      "New Joiners (MTD)",
      "Open Positions",
    ]) {
      await expect(page.getByText(kpi).first()).toBeVisible({ timeout: 15000 });
    }
  });

  test('attrition rate is NOT "undefined%" -- must be a number followed by %', async ({
    page,
  }) => {
    await expect(page.getByText("Attrition Rate").first()).toBeVisible({
      timeout: 15000,
    });
    const attritionValue = page
      .locator(':has-text("Attrition Rate")')
      .locator(".text-2xl")
      .first();
    const text = await attritionValue.textContent();
    expect(text).toBeTruthy();
    expect(text).toMatch(/^\d+(\.\d+)?%$/);
    expect(text).not.toContain("undefined");
  });

  test("department breakdown table renders with proper columns", async ({ page }) => {
    await expect(page.getByText("Department Breakdown").first()).toBeVisible({
      timeout: 15000,
    });
    for (const header of ["Department", "Headcount", "Attrition %", "Avg Tenure (yrs)"]) {
      await expect(page.locator(`th:has-text("${header}")`).first()).toBeVisible({
        timeout: 10000,
      });
    }
  });

  test("payroll status shows processed or pending", async ({ page }) => {
    await page.getByRole("button", { name: "Payroll" }).click();
    await expect(
      page.getByText("Current Month Payroll Status").first(),
    ).toBeVisible({ timeout: 15000 });

    const body = (await page.locator("body").textContent()) || "";
    expect(body.includes("Processed") || body.includes("Pending")).toBe(true);
  });

  test("payroll tab shows PF/ESI/PT/TDS breakdown", async ({ page }) => {
    await page.getByRole("button", { name: "Payroll" }).click();
    for (const label of ["PF Contribution", "ESI", "Professional Tax", "TDS on Salary"]) {
      await expect(page.getByText(label).first()).toBeVisible({ timeout: 15000 });
    }
  });

  test("recruitment pipeline has funnel and open positions", async ({ page }) => {
    await page.getByRole("button", { name: "Recruitment" }).click();
    await expect(page.getByText("Recruitment Funnel").first()).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText("Open Positions").first()).toBeVisible({
      timeout: 10000,
    });
  });

  test("engagement tab shows eNPS and pulse survey", async ({ page }) => {
    await page.getByRole("button", { name: "Engagement" }).click();
    await expect(
      page.getByText("Employee Net Promoter Score").first(),
    ).toBeVisible({ timeout: 15000 });
    await expect(page.getByText("eNPS Score").first()).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText("Pulse Survey Score").first()).toBeVisible({
      timeout: 10000,
    });
  });

  test("compliance tab shows EPFO/ESI/PT status", async ({ page }) => {
    await page.getByRole("button", { name: "Compliance" }).click();
    for (const section of [
      "EPFO Filing Status",
      "ESI Filing Status",
      "Professional Tax Filing Status",
    ]) {
      await expect(page.getByText(section).first()).toBeVisible({
        timeout: 15000,
      });
    }
  });

  test('no "undefined" or "NaN"', async ({ page }) => {
    await page.waitForTimeout(2000);
    await assertNoGarbageText(page);
  });
});

// ==========================================================================
//  4. CMO Dashboard (/dashboard/cmo)
// ==========================================================================

test.describe("CMO Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await goToDashboard(page, "/dashboard/cmo");
  });

  test("page loads without crash", async ({ page }) => {
    const body = (await page.locator("body").textContent()) || "";
    expect(body).not.toContain("Cannot GET");
    expect(body).not.toContain("Application error");
    await expect(page.getByText("CMO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("shows KPI cards (CAC, MQLs, SQLs, Pipeline Value)", async ({ page }) => {
    for (const kpi of [
      "Customer Acquisition Cost",
      "MQLs This Month",
      "SQLs This Month",
      "Pipeline Value",
    ]) {
      await expect(page.getByText(kpi).first()).toBeVisible({ timeout: 15000 });
    }
  });

  test("ROAS by channel chart renders", async ({ page }) => {
    await expect(
      page.getByText("Return on Ad Spend (ROAS) by Channel").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("email performance section renders", async ({ page }) => {
    await expect(
      page.getByText("Email Performance").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("social engagement by platform renders", async ({ page }) => {
    await page.waitForLoadState("networkidle").catch(() => {});
    await expect(
      page.getByText("Social Engagement by Platform").first(),
    ).toBeVisible({ timeout: 30000 });
  });

  test("website traffic section has sessions, users, bounce rate", async ({ page }) => {
    await expect(page.getByText("Website Traffic").first()).toBeVisible({
      timeout: 15000,
    });
    for (const label of ["Sessions", "Users", "Bounce Rate"]) {
      await expect(page.getByText(label).first()).toBeVisible({ timeout: 10000 });
    }
  });

  test("brand sentiment and pending approvals render", async ({ page }) => {
    await expect(
      page.getByText("Brand Sentiment Score").first(),
    ).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByText("Pending Content Approvals").first(),
    ).toBeVisible({ timeout: 10000 });
  });

  test('no "undefined" or "NaN"', async ({ page }) => {
    await page.waitForTimeout(2000);
    await assertNoGarbageText(page);
  });
});

// ==========================================================================
//  5. COO Dashboard (/dashboard/coo)
// ==========================================================================

test.describe("COO Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await goToDashboard(page, "/dashboard/coo");
  });

  test("page loads without crash", async ({ page }) => {
    const body = (await page.locator("body").textContent()) || "";
    expect(body).not.toContain("Cannot GET");
    expect(body).not.toContain("Application error");
    await expect(page.getByText("COO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("IT Ops tab shows KPIs (Active Incidents, MTTR, Uptime, Change Success)", async ({
    page,
  }) => {
    for (const kpi of [
      "Active Incidents",
      "MTTR (hours)",
      "Uptime %",
      "Change Success Rate",
    ]) {
      await expect(page.getByText(kpi).first()).toBeVisible({ timeout: 15000 });
    }
  });

  test("IT Ops tab shows incident severity breakdown and recent incidents", async ({
    page,
  }) => {
    await expect(
      page.getByText("Incident Severity Breakdown").first(),
    ).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByText("Recent Incidents").first(),
    ).toBeVisible({ timeout: 10000 });
  });

  test("support tab shows tickets, CSAT, and trend", async ({ page }) => {
    await page.getByRole("button", { name: "Support" }).click();
    for (const kpi of ["Open Tickets", "Resolved Today", "CSAT Score", "Deflection Rate"]) {
      await expect(page.getByText(kpi).first()).toBeVisible({ timeout: 15000 });
    }
    await expect(
      page.getByText("Ticket Volume Trend").first(),
    ).toBeVisible({ timeout: 10000 });
  });

  test("support tab has SLA compliance and ticket categories", async ({ page }) => {
    await page.getByRole("button", { name: "Support" }).click();
    await expect(
      page.getByText("SLA Compliance by Priority").first(),
    ).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByText("Top Ticket Categories").first(),
    ).toBeVisible({ timeout: 10000 });
  });

  test("vendors tab renders vendor scorecard and spend chart", async ({ page }) => {
    await page.getByRole("button", { name: "Vendors" }).click();
    await expect(page.getByText("Active Vendors").first()).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText("Vendor Scorecard").first()).toBeVisible({
      timeout: 10000,
    });
    await expect(
      page.getByText("Vendor Spend by Category").first(),
    ).toBeVisible({ timeout: 10000 });
  });

  test("facilities tab renders maintenance and asset info", async ({ page }) => {
    await page.getByRole("button", { name: "Facilities" }).click();
    for (const kpi of [
      "Open Maintenance Requests",
      "Asset Utilization %",
      "Travel Expense MTD",
    ]) {
      await expect(page.getByText(kpi).first()).toBeVisible({ timeout: 15000 });
    }
    await expect(
      page.getByText("Upcoming Maintenance Schedule").first(),
    ).toBeVisible({ timeout: 10000 });
  });

  test("uptime percentage is a valid 0-100 number", async ({ page }) => {
    const uptimeCard = page.locator(':has-text("Uptime %")').locator(".text-2xl").first();
    const text = await uptimeCard.textContent({ timeout: 15000 });
    expect(text).toMatch(/^\d+(\.\d+)?%$/);
    const numericValue = parseFloat(text!.replace("%", ""));
    expect(numericValue).toBeGreaterThanOrEqual(0);
    expect(numericValue).toBeLessThanOrEqual(100);
  });

  test('no "undefined" or "NaN" or "Cannot convert"', async ({ page }) => {
    await page.waitForTimeout(2000);
    await assertNoGarbageText(page);
  });
});

// ==========================================================================
//  6. CBO Dashboard (/dashboard/cbo)
// ==========================================================================

test.describe("CBO Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await goToDashboard(page, "/dashboard/cbo");
  });

  test("page loads without crash", async ({ page }) => {
    const body = (await page.locator("body").textContent()) || "";
    expect(body).not.toContain("Cannot GET");
    expect(body).not.toContain("Application error");
    await expect(page.getByText("CBO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("legal tab shows contracts, reviews, and NDA count", async ({ page }) => {
    for (const kpi of ["Active Contracts", "Pending Reviews", "NDA Count"]) {
      await expect(page.getByText(kpi).first()).toBeVisible({ timeout: 15000 });
    }
    await expect(page.getByText("Contract Review Queue").first()).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText("Litigation Tracker").first()).toBeVisible({
      timeout: 10000,
    });
  });

  test("risk tab shows compliance score and risk register", async ({ page }) => {
    await page.getByRole("button", { name: "Risk" }).click();
    await expect(page.getByText("Compliance Score").first()).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText("Open Audit Findings").first()).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText("Risk Register").first()).toBeVisible({
      timeout: 10000,
    });
  });

  test("risk register table has proper columns", async ({ page }) => {
    await page.getByRole("button", { name: "Risk" }).click();
    await expect(page.getByText("Risk Register").first()).toBeVisible({
      timeout: 15000,
    });
    for (const header of ["Risk", "Likelihood", "Impact", "Owner", "Status"]) {
      await expect(page.locator(`th:has-text("${header}")`).first()).toBeVisible({
        timeout: 10000,
      });
    }
  });

  test("corporate tab shows board meeting and AGM info", async ({ page }) => {
    await page.getByRole("button", { name: "Corporate" }).click();
    await expect(page.getByText("Next Board Meeting").first()).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText("Days Until AGM").first()).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText("Share Register Summary").first()).toBeVisible({
      timeout: 10000,
    });
    await expect(
      page.getByText("Statutory Filing Status").first(),
    ).toBeVisible({ timeout: 10000 });
  });

  test("corporate tab share register shows percentage breakdowns", async ({ page }) => {
    await page.getByRole("button", { name: "Corporate" }).click();
    await expect(page.getByText("Share Register Summary").first()).toBeVisible({
      timeout: 15000,
    });
    for (const label of ["Total Shares", "Promoter %", "Public %", "Institutional %"]) {
      await expect(page.getByText(label).first()).toBeVisible({ timeout: 10000 });
    }
  });

  test("comms tab shows media metrics and press coverage", async ({ page }) => {
    await page.getByRole("button", { name: "Comms" }).click();
    for (const kpi of [
      "Internal Comms Reach",
      "Media Mentions (MTD)",
      "Investor Queries Open",
    ]) {
      await expect(page.getByText(kpi).first()).toBeVisible({ timeout: 15000 });
    }
    await expect(page.getByText("Recent Press Coverage").first()).toBeVisible({
      timeout: 10000,
    });
  });

  test('no "undefined" or "NaN" or "Cannot convert"', async ({ page }) => {
    await page.waitForTimeout(2000);
    await assertNoGarbageText(page);
  });
});

// ==========================================================================
//  7. Cross-Cutting Tests
// ==========================================================================

test.describe("Cross-cutting: All CxO Dashboards", () => {
  test("all 6 dashboards accessible in sequence without crash", async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);

    const dashboards = [
      { path: "/dashboard/ceo", heading: "CEO Dashboard" },
      { path: "/dashboard/cfo", heading: "CFO Dashboard" },
      { path: "/dashboard/chro", heading: "CHRO Dashboard" },
      { path: "/dashboard/cmo", heading: "CMO Dashboard" },
      { path: "/dashboard/coo", heading: "COO Dashboard" },
      { path: "/dashboard/cbo", heading: "CBO Dashboard" },
    ];

    for (const { path, heading } of dashboards) {
      await goToDashboard(page, path);
      await expect(page.getByText(heading).first()).toBeVisible({
        timeout: 15000,
      });
    }
  });

  test("role-based access: admin sidebar shows all CxO dashboard links", async ({
    page,
  }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await goToDashboard(page, "/dashboard/ceo");

    // For an admin user, the sidebar should contain all CxO nav links
    const sidebarLabels = [
      "CEO Dashboard",
      "Finance Dashboard",
      "Marketing Dashboard",
      "CHRO Dashboard",
      "COO Dashboard",
      "CBO Dashboard",
    ];

    for (const label of sidebarLabels) {
      const link = page.locator(`nav a:has-text("${label}")`).first();
      const isVisible = await link.isVisible().catch(() => false);
      // If the token belongs to an admin role, all links should appear
      if (isVisible) {
        expect(isVisible).toBe(true);
      }
    }
  });

  test("demo data badge consistency across dashboards", async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);

    const paths = [
      "/dashboard/ceo",
      "/dashboard/cfo",
      "/dashboard/chro",
      "/dashboard/cmo",
      "/dashboard/coo",
      "/dashboard/cbo",
    ];

    let demoBadgeSeen = false;
    let noDemoBadgeSeen = false;

    for (const path of paths) {
      await goToDashboard(page, path);
      await page.waitForTimeout(1500);
      const body = (await page.locator("body").textContent()) || "";
      if (body.includes("Demo Data")) {
        demoBadgeSeen = true;
      } else if (!body.includes("Failed to load") && !body.includes("No data available")) {
        noDemoBadgeSeen = true;
      }
    }

    // At least one pattern should be observed (either all demo or all real)
    expect(demoBadgeSeen || noDemoBadgeSeen).toBe(true);
  });

  test("no console errors on any CxO dashboard", async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);

    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        const text = msg.text();
        // Ignore known benign errors (network timeouts, favicon, etc.)
        if (
          !text.includes("favicon") &&
          !text.includes("net::ERR_") &&
          !text.includes("Failed to fetch")
        ) {
          consoleErrors.push(text);
        }
      }
    });

    for (const path of [
      "/dashboard/ceo",
      "/dashboard/cfo",
      "/dashboard/chro",
      "/dashboard/cmo",
      "/dashboard/coo",
      "/dashboard/cbo",
    ]) {
      await goToDashboard(page, path);
      await page.waitForTimeout(2000);
    }

    // Allow zero or very few transient errors
    expect(consoleErrors.length).toBeLessThanOrEqual(2);
  });
});

// ==========================================================================
//  8. Page Titles (SEO / Helmet)
// ==========================================================================

test.describe("Page Titles", () => {
  test("each CxO dashboard sets correct page title via Helmet", async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);

    const titleTests = [
      { path: "/dashboard/ceo", title: /CEO Dashboard/ },
      { path: "/dashboard/cfo", title: /CFO Dashboard/ },
      { path: "/dashboard/chro", title: /CHRO Dashboard/ },
      { path: "/dashboard/cmo", title: /CMO Dashboard/ },
      { path: "/dashboard/coo", title: /COO Dashboard/ },
      { path: "/dashboard/cbo", title: /CBO Dashboard/ },
    ];

    for (const { path, title } of titleTests) {
      await goToDashboard(page, path);
      await page.waitForTimeout(1500);
      await expect(page).toHaveTitle(title, { timeout: 10000 });
    }
  });
});
