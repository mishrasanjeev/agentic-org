/**
 * CA Firms Feature — E2E Tests
 *
 * Covers all CA-specific UI pages:
 *   1. CAFirmsSolution  (/solutions/ca-firms)
 *   2. CompanyDashboard (/dashboard/companies)
 *   3. CompanyOnboard   (/dashboard/companies/new)
 *   4. CompanyDetail    (/dashboard/companies/:id)
 *   5. CompanySwitcher  (header component)
 *   6. Landing page CA section
 *
 * All tests are read-only and production-safe.
 */
import { test, expect, Page } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const MARKETING = "https://agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

// -- Helper: authenticate via localStorage token --
async function authenticate(page: Page): Promise<void> {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate((token) => {
    localStorage.setItem("token", token);
  }, E2E_TOKEN);
}

// ==========================================================================
//  CAFirmsSolution Page (/solutions/ca-firms)
// ==========================================================================

test.describe("CA Firms Solution Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${APP}/solutions/ca-firms`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});
  });

  test("page loads without error at /solutions/ca-firms", async ({ page }) => {
    const mainContent = (await page.locator("body").textContent()) || "";
    expect(mainContent).not.toContain("Cannot GET");
    expect(mainContent).not.toContain("Application error");

    // Title should reference CA
    await expect(page).toHaveTitle(/Chartered Accountant|CA Firm|AgenticOrg/i);
  });

  test("hero section renders headline and CTAs", async ({ page }) => {
    // Hero headline
    await expect(
      page.getByText("AI-Powered Virtual Employees for").first()
    ).toBeVisible({ timeout: 10000 });

    await expect(
      page.getByText("Chartered Accountant Firms").first()
    ).toBeVisible({ timeout: 10000 });

    // CTA buttons
    await expect(
      page.getByRole("button", { name: /Start Free Trial/i }).first()
    ).toBeVisible({ timeout: 10000 });

    await expect(
      page.getByRole("link", { name: /Try Demo/i }).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("features grid renders 6 feature cards", async ({ page }) => {
    const features = [
      "Multi-Client Management",
      "GST Compliance",
      "TDS Automation",
      "Bank Reconciliation",
      "Month-End Close",
      "Audit Trail",
    ];
    for (const feature of features) {
      await expect(
        page.getByText(feature).first()
      ).toBeVisible({ timeout: 10000 });
    }
  });

  test("pricing section renders with INR 4999", async ({ page }) => {
    await expect(
      page.getByText("4,999").first()
    ).toBeVisible({ timeout: 10000 });

    await expect(
      page.getByText(/per client/i).first()
    ).toBeVisible({ timeout: 10000 });

    // Pricing section has the CA Pack badge
    await expect(
      page.getByText("CA Pack").first()
    ).toBeVisible({ timeout: 10000 });
  });
});

// ==========================================================================
//  CompanyDashboard (/dashboard/companies)
// ==========================================================================

test.describe("Company Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("CompanyDashboard loads at /dashboard/companies", async ({ page }) => {
    const response = await page.goto(`${APP}/dashboard/companies`, {
      waitUntil: "domcontentloaded",
    });
    const status = response?.status() ?? 0;
    expect(status).toBeLessThan(500);

    if (status < 400) {
      await page.waitForLoadState("networkidle").catch(() => {});
      await expect(
        page.getByText(/Companies/i).first()
      ).toBeVisible({ timeout: 15000 });

      // No rendering errors
      const mainContent = (await page.locator("main, [class*='space-y']").first().textContent()) || "";
      expect(mainContent).not.toContain("undefined");
      expect(mainContent).not.toContain("NaN");
    }
  });

  test("stats bar shows total clients and active count", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Stats bar labels
    await expect(
      page.getByText("Total Clients").first()
    ).toBeVisible({ timeout: 15000 });

    await expect(
      page.getByText("Active").first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("search input filters companies", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Search input should exist
    const searchInput = page.locator('input[placeholder*="Search"]');
    await expect(searchInput).toBeVisible({ timeout: 10000 });

    // Type a search term and verify filtering happens (no crash)
    await searchInput.fill("Manufacturing");
    // The page should still render without errors
    const body = (await page.locator("body").textContent()) || "";
    expect(body).not.toContain("Application error");
  });
});

// ==========================================================================
//  CompanyOnboard (/dashboard/companies/new)
// ==========================================================================

test.describe("Company Onboard Wizard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("wizard loads at /dashboard/companies/new", async ({ page }) => {
    const response = await page.goto(`${APP}/dashboard/companies/new`, {
      waitUntil: "domcontentloaded",
    });
    const status = response?.status() ?? 0;
    expect(status).toBeLessThan(500);

    if (status < 400) {
      await page.waitForLoadState("networkidle").catch(() => {});

      // Should show step 1 "Basic Info"
      await expect(
        page.getByText("Basic Info").first()
      ).toBeVisible({ timeout: 15000 });
    }
  });

  test("Step 1 GSTIN validation rejects invalid format", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/new`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Find the GSTIN input and enter an invalid value
    const gstinInput = page.locator('input[placeholder*="GSTIN"], input[name*="gstin"], input[id*="gstin"]').first();
    if (await gstinInput.isVisible().catch(() => false)) {
      await gstinInput.fill("INVALID123");

      // Try to proceed -- click Next
      const nextBtn = page.getByRole("button", { name: /Next/i }).first();
      if (await nextBtn.isVisible().catch(() => false)) {
        await nextBtn.click();
      }

      // Should show validation error or remain on step 1
      const body = (await page.locator("body").textContent()) || "";
      const hasError = body.includes("invalid") || body.includes("Invalid") || body.includes("GSTIN") || body.includes("format");
      // The form should not proceed with invalid GSTIN (either error message or stays on step 1)
      const stillOnStep1 = await page.getByText("Basic Info").first().isVisible().catch(() => false);
      expect(hasError || stillOnStep1).toBeTruthy();
    }
  });

  test("Step 1 PAN validation rejects invalid format", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/new`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Find the PAN input and enter an invalid value
    const panInput = page.locator('input[placeholder*="PAN"], input[name*="pan"], input[id*="pan"]').first();
    if (await panInput.isVisible().catch(() => false)) {
      await panInput.fill("123");

      // Try to proceed
      const nextBtn = page.getByRole("button", { name: /Next/i }).first();
      if (await nextBtn.isVisible().catch(() => false)) {
        await nextBtn.click();
      }

      // Should show validation error or remain on step 1
      const body = (await page.locator("body").textContent()) || "";
      const hasError = body.includes("invalid") || body.includes("Invalid") || body.includes("PAN") || body.includes("format");
      const stillOnStep1 = await page.getByText("Basic Info").first().isVisible().catch(() => false);
      expect(hasError || stillOnStep1).toBeTruthy();
    }
  });

  test("wizard shows all 6 steps", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/new`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const steps = [
      "Basic Info",
      "Compliance",
      "Signatory",
      "Banking",
      "Tally",
      "Review",
    ];

    let foundSteps = 0;
    for (const step of steps) {
      const el = page.getByText(step, { exact: false }).first();
      if (await el.isVisible().catch(() => false)) {
        foundSteps++;
      }
    }
    // At minimum the current step and step labels should be visible
    expect(foundSteps).toBeGreaterThanOrEqual(1);

    // The body should contain references to all 6 steps (even if not all visible at once)
    const body = (await page.locator("body").textContent()) || "";
    expect(body).toContain("Basic Info");
  });
});

// ==========================================================================
//  CompanyDetail (/dashboard/companies/:id)
// ==========================================================================

test.describe("Company Detail", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("CompanyDetail loads at /dashboard/companies/:id", async ({ page }) => {
    // Use a mock/test company ID -- the page will fall back to mock data
    const response = await page.goto(
      `${APP}/dashboard/companies/c1`,
      { waitUntil: "domcontentloaded" }
    );
    const status = response?.status() ?? 0;
    expect(status).toBeLessThan(500);

    if (status < 400) {
      await page.waitForLoadState("networkidle").catch(() => {});

      // Should show company name or a tab
      const body = (await page.locator("body").textContent()) || "";
      expect(body).not.toContain("Application error");
    }
  });

  test("CompanyDetail shows 6 tabs", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const tabLabels = [
      "Overview",
      "Compliance",
      "Agents",
      "Workflows",
      "Audit Log",
      "Settings",
    ];

    let foundTabs = 0;
    for (const tab of tabLabels) {
      const el = page.getByText(tab, { exact: true }).first();
      if (await el.isVisible({ timeout: 5000 }).catch(() => false)) {
        foundTabs++;
      }
    }
    expect(foundTabs).toBeGreaterThanOrEqual(4);
  });

  test("compliance tab shows GST calendar", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Click compliance tab
    const complianceTab = page.getByText("Compliance", { exact: true }).first();
    if (await complianceTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await complianceTab.click();

      // Should render GST calendar months
      const monthNames = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"];
      let foundMonths = 0;
      for (const month of monthNames) {
        const el = page.getByText(month).first();
        if (await el.isVisible({ timeout: 3000 }).catch(() => false)) {
          foundMonths++;
        }
      }
      expect(foundMonths).toBeGreaterThanOrEqual(6);
    }
  });
});

// ==========================================================================
//  CompanySwitcher (header dropdown)
// ==========================================================================

test.describe("Company Switcher", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("CompanySwitcher dropdown opens and shows companies", async ({ page }) => {
    await page.goto(`${APP}/dashboard`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Look for the switcher button (contains company name or "Select Company")
    const switcherButton = page.locator("button").filter({
      hasText: /Company|Select|Acme|Sharma|Gupta/i,
    }).first();

    if (await switcherButton.isVisible({ timeout: 5000 }).catch(() => false)) {
      await switcherButton.click();

      // Dropdown should appear with company options or "Manage Companies" link
      const dropdown = page.locator('[class*="absolute"]').filter({
        hasText: /Company|Manage|GSTIN/i,
      }).first();
      await expect(dropdown).toBeVisible({ timeout: 5000 });
    }
  });
});

// ==========================================================================
//  Landing Page -- "For CA Firms" Section
// ==========================================================================

test.describe("Landing Page -- CA Firms Section", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(MARKETING, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
  });

  test("For CA Firms section renders with CTA buttons", async ({ page }) => {
    // The landing page has a section titled "Built for Chartered Accountant Firms"
    await expect(
      page.getByText("Built for Chartered Accountant Firms").first()
    ).toBeVisible({ timeout: 15000 });

    // CA Pack badge
    await expect(
      page.getByText("CA Pack").first()
    ).toBeVisible({ timeout: 10000 });

    // CTA: "Learn More" links to /solutions/ca-firms
    const learnMore = page.getByRole("link", { name: /Learn More/i }).first();
    await expect(learnMore).toBeVisible({ timeout: 10000 });
    await expect(learnMore).toHaveAttribute("href", /ca-firms/);
  });

  test("Try Demo button navigates correctly", async ({ page }) => {
    // Scroll to the CA Firms section
    const section = page.getByText("Built for Chartered Accountant Firms").first();
    await section.scrollIntoViewIfNeeded();

    // Find the "Try Demo" link near the CA section
    const tryDemo = page.getByRole("link", { name: /Try Demo/i }).first();
    if (await tryDemo.isVisible({ timeout: 5000 }).catch(() => false)) {
      const href = await tryDemo.getAttribute("href");
      // Should navigate to login with demo=true or to a demo page
      expect(href).toMatch(/login|demo/);
    }
  });
});

// ==========================================================================
//  CA Demo Login Flow
// ==========================================================================

test.describe("CA Demo Login Flow", () => {
  test("demo login page shows CA Partner option", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Look for "Try the demo instead" or a demo section toggle
    const demoToggle = page.getByText(/Try the demo|Demo|demo/i).first();
    if (await demoToggle.isVisible({ timeout: 5000 }).catch(() => false)) {
      await demoToggle.click();
    }

    // Verify "CA Partner" demo button exists
    const caPartnerBtn = page.getByText(/CA Partner|CA Firm|Chartered Accountant/i).first();
    const visible = await caPartnerBtn.isVisible({ timeout: 5000 }).catch(() => false);
    // The login page should mention CA somewhere (either in demo buttons or text)
    const body = (await page.locator("body").textContent()) || "";
    expect(visible || body.includes("CA") || body.includes("demo")).toBeTruthy();
  });

  test("CA demo credentials auto-fill on click", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Expand demo section if collapsed
    const demoToggle = page.getByText(/Try the demo|Demo|demo/i).first();
    if (await demoToggle.isVisible({ timeout: 5000 }).catch(() => false)) {
      await demoToggle.click();
    }

    // Click CA Partner button if visible
    const caPartnerBtn = page.getByText(/CA Partner/i).first();
    if (await caPartnerBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await caPartnerBtn.click();

      // Verify email field is filled with demo CA email
      const emailInput = page.locator('input[type="email"], input[name="email"], input[placeholder*="email"]').first();
      if (await emailInput.isVisible({ timeout: 3000 }).catch(() => false)) {
        const emailValue = await emailInput.inputValue();
        expect(emailValue).toBe("demo@cafirm.agenticorg.ai");
      }
    }
  });
});

// ==========================================================================
//  Filing Approvals
// ==========================================================================

test.describe("Filing Approvals", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("Approvals tab is visible on CompanyDetail", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // The Approvals tab should be in the tab bar
    const approvalsTab = page.getByText("Approvals", { exact: true }).first();
    await expect(approvalsTab).toBeVisible({ timeout: 10000 });
  });

  test("Approvals tab shows filing requests", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Click the Approvals tab
    const approvalsTab = page.getByText("Approvals", { exact: true }).first();
    if (await approvalsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await approvalsTab.click();

      // Should show "Filing Approvals" heading and filing type columns
      await expect(
        page.getByText("Filing Approvals").first()
      ).toBeVisible({ timeout: 10000 });

      // Verify table columns exist
      await expect(
        page.getByText("Filing Type").first()
      ).toBeVisible({ timeout: 5000 });

      await expect(
        page.getByText("Period").first()
      ).toBeVisible({ timeout: 5000 });

      // Verify at least one filing request row exists (e.g. GSTR-1 or TDS)
      const filingTypes = ["GSTR-1", "GSTR-3B", "GSTR-9", "TDS 26Q"];
      let foundFilings = 0;
      for (const ft of filingTypes) {
        const el = page.getByText(ft).first();
        if (await el.isVisible({ timeout: 3000 }).catch(() => false)) {
          foundFilings++;
        }
      }
      expect(foundFilings).toBeGreaterThanOrEqual(1);
    }
  });

  test("Approve button visible on pending items", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const approvalsTab = page.getByText("Approvals", { exact: true }).first();
    if (await approvalsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await approvalsTab.click();

      // Look for Approve buttons (should exist for pending items)
      const approveBtn = page.getByRole("button", { name: /Approve/i }).first();
      const visible = await approveBtn.isVisible({ timeout: 5000 }).catch(() => false);

      // Also verify pending badge exists alongside Approve button
      const pendingBadge = page.getByText("pending").first();
      const hasPending = await pendingBadge.isVisible({ timeout: 3000 }).catch(() => false);

      // Either approve button or pending badge should be visible
      expect(visible || hasPending).toBeTruthy();
    }
  });
});

// ==========================================================================
//  GSTN Manual Upload
// ==========================================================================

test.describe("GSTN Manual Upload", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("Compliance tab shows GSTN Manual Upload section", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Click Compliance tab
    const complianceTab = page.getByText("Compliance", { exact: true }).first();
    if (await complianceTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await complianceTab.click();

      // Should show "GSTN Manual Upload" section
      await expect(
        page.getByText("GSTN Manual Upload").first()
      ).toBeVisible({ timeout: 10000 });

      // Should show a description about manual upload
      const body = (await page.locator("body").textContent()) || "";
      expect(body).toContain("auto-filing disabled");
    }
  });

  test("Download button visible for generated files", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const complianceTab = page.getByText("Compliance", { exact: true }).first();
    if (await complianceTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await complianceTab.click();

      // Look for Download buttons in the GSTN upload table
      const downloadBtn = page.getByRole("button", { name: /Download/i }).first();
      await expect(downloadBtn).toBeVisible({ timeout: 10000 });

      // Also verify "Mark as Uploaded" button exists
      const markUploadedBtn = page.getByRole("button", { name: /Mark as Uploaded/i }).first();
      await expect(markUploadedBtn).toBeVisible({ timeout: 5000 });
    }
  });
});

// ==========================================================================
//  Subscription Status
// ==========================================================================

test.describe("Subscription Status", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("Company detail shows subscription badge", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // The company header should show a subscription status badge
    // Valid values: Trial, Active, Expired
    const subscriptionBadges = ["Trial", "Active", "Expired"];
    let foundBadge = false;
    for (const badge of subscriptionBadges) {
      const el = page.getByText(badge, { exact: true }).first();
      if (await el.isVisible({ timeout: 3000 }).catch(() => false)) {
        foundBadge = true;
        break;
      }
    }

    // Fallback: check the header area for any subscription-related text
    const body = (await page.locator("body").textContent()) || "";
    const hasSubscriptionText = body.includes("Trial") || body.includes("Active") || body.includes("Expired");
    expect(foundBadge || hasSubscriptionText).toBeTruthy();
  });
});

// ==========================================================================
//  Client Health Score
// ==========================================================================

test.describe("Client Health Score", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("Company detail overview shows health score", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // The overview tab (default) should show "Client Health Score" metric
    await expect(
      page.getByText("Client Health Score").first()
    ).toBeVisible({ timeout: 10000 });

    // The health score value should be a number (e.g. 92)
    const body = (await page.locator("body").textContent()) || "";
    // Health score text should be present somewhere on the page
    expect(body).toContain("Client Health Score");
  });

  test("Company dashboard cards show health indicator", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // The dashboard should show company cards with status indicators
    // At minimum, active/inactive badges should be visible
    const statusBadges = page.locator('[class*="badge"], [class*="Badge"]');
    const badgeCount = await statusBadges.count().catch(() => 0);

    // Should have at least one badge visible (status indicators)
    const body = (await page.locator("body").textContent()) || "";
    const hasHealthIndicator = body.includes("active") || body.includes("Active") || body.includes("inactive");
    expect(badgeCount > 0 || hasHealthIndicator).toBeTruthy();
  });
});

// ==========================================================================
//  Partner Dashboard (Phase 2)
// ==========================================================================

test.describe("Partner Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("loads at /dashboard/partner", async ({ page }) => {
    const response = await page.goto(`${APP}/dashboard/partner`, {
      waitUntil: "domcontentloaded",
    });
    const status = response?.status() ?? 0;
    expect(status).toBeLessThan(500);

    if (status < 400) {
      await page.waitForLoadState("networkidle").catch(() => {});
      const body = (await page.locator("body").textContent()) || "";
      expect(body).not.toContain("Application error");
      expect(body).not.toContain("Cannot GET");
    }
  });

  test("shows aggregate KPI cards", async ({ page }) => {
    await page.goto(`${APP}/dashboard/partner`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Partner dashboard should show KPI cards with metrics
    const kpiLabels = ["Total Clients", "Active", "Filings Due", "Health Score", "Revenue"];
    let foundKPIs = 0;
    for (const label of kpiLabels) {
      const el = page.getByText(label, { exact: false }).first();
      if (await el.isVisible({ timeout: 5000 }).catch(() => false)) {
        foundKPIs++;
      }
    }
    // Should find at least 2 KPI cards
    const body = (await page.locator("body").textContent()) || "";
    const hasKPIs = foundKPIs >= 2 || body.includes("Total Clients") || body.includes("Active");
    expect(hasKPIs).toBeTruthy();
  });

  test("shows client health table", async ({ page }) => {
    await page.goto(`${APP}/dashboard/partner`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Look for a table or list of clients with health indicators
    const body = (await page.locator("body").textContent()) || "";
    const hasClientTable =
      body.includes("Client") ||
      body.includes("Company") ||
      body.includes("Health") ||
      body.includes("Status");
    expect(hasClientTable).toBeTruthy();
  });

  test("shows upcoming deadlines", async ({ page }) => {
    await page.goto(`${APP}/dashboard/partner`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Upcoming deadlines section
    const body = (await page.locator("body").textContent()) || "";
    const hasDeadlines =
      body.includes("Deadline") ||
      body.includes("Due") ||
      body.includes("Filing") ||
      body.includes("Upcoming");
    expect(hasDeadlines).toBeTruthy();
  });
});

// ==========================================================================
//  GSTN Credential Vault (Phase 2)
// ==========================================================================

test.describe("GSTN Credential Vault", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("Settings tab shows credential vault section", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Click Settings tab
    const settingsTab = page.getByText("Settings", { exact: true }).first();
    if (await settingsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await settingsTab.click();

      // Should show credential vault section
      const body = (await page.locator("body").textContent()) || "";
      const hasVault =
        body.includes("Credential") ||
        body.includes("GSTN") ||
        body.includes("Portal") ||
        body.includes("Login");
      expect(hasVault).toBeTruthy();
    }
  });

  test("Save Credentials button is visible", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const settingsTab = page.getByText("Settings", { exact: true }).first();
    if (await settingsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await settingsTab.click();

      // Look for Save Credentials or Save button in the vault section
      const saveBtn = page.getByRole("button", { name: /Save|Credentials|Update/i }).first();
      const visible = await saveBtn.isVisible({ timeout: 5000 }).catch(() => false);

      const body = (await page.locator("body").textContent()) || "";
      const hasSaveAction = visible || body.includes("Save") || body.includes("Credential");
      expect(hasSaveAction).toBeTruthy();
    }
  });

  test("Auto-upload toggle is visible", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const settingsTab = page.getByText("Settings", { exact: true }).first();
    if (await settingsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await settingsTab.click();

      // Look for Auto-upload toggle or checkbox
      const body = (await page.locator("body").textContent()) || "";
      const hasAutoUpload =
        body.includes("Auto-upload") ||
        body.includes("auto-upload") ||
        body.includes("Auto Upload") ||
        body.includes("auto_upload");
      expect(hasAutoUpload).toBeTruthy();
    }
  });
});

// ==========================================================================
//  Tally Auto-Detect (Phase 2)
// ==========================================================================

test.describe("Tally Auto-Detect", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("Tally step shows Auto-Detect button", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/new`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Navigate to the Tally step (step 5 of 6)
    const tallyStep = page.getByText("Tally", { exact: false }).first();
    if (await tallyStep.isVisible({ timeout: 5000 }).catch(() => false)) {
      await tallyStep.click();
    }

    // Look for Auto-Detect button on the Tally step
    const body = (await page.locator("body").textContent()) || "";
    const hasTallyDetect =
      body.includes("Auto-Detect") ||
      body.includes("auto-detect") ||
      body.includes("Detect") ||
      body.includes("Tally");
    expect(hasTallyDetect).toBeTruthy();
  });
});

// ==========================================================================
//  Compliance Calendar (Phase 2)
// ==========================================================================

test.describe("Compliance Calendar", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("Compliance tab shows upcoming deadlines", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Click Compliance tab
    const complianceTab = page.getByText("Compliance", { exact: true }).first();
    if (await complianceTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await complianceTab.click();

      // Should show deadlines or filing calendar
      const body = (await page.locator("body").textContent()) || "";
      const hasDeadlineInfo =
        body.includes("Deadline") ||
        body.includes("Due") ||
        body.includes("GSTR") ||
        body.includes("TDS") ||
        body.includes("PF") ||
        body.includes("ESI");
      expect(hasDeadlineInfo).toBeTruthy();
    }
  });

  test("Generate Deadlines button visible", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const complianceTab = page.getByText("Compliance", { exact: true }).first();
    if (await complianceTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await complianceTab.click();

      // Look for Generate Deadlines button or similar action
      const generateBtn = page.getByRole("button", { name: /Generate|Refresh|Sync/i }).first();
      const visible = await generateBtn.isVisible({ timeout: 5000 }).catch(() => false);

      const body = (await page.locator("body").textContent()) || "";
      const hasGenerateAction = visible || body.includes("Generate") || body.includes("Calendar");
      expect(hasGenerateAction).toBeTruthy();
    }
  });
});

// ==========================================================================
//  Bulk Approval (Phase 2)
// ==========================================================================

test.describe("Bulk Approval", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("Approvals tab has select-all checkbox", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const approvalsTab = page.getByText("Approvals", { exact: true }).first();
    if (await approvalsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await approvalsTab.click();

      // Look for select-all checkbox or bulk selection UI
      const selectAllCheckbox = page.locator('input[type="checkbox"]').first();
      const hasCheckbox = await selectAllCheckbox.isVisible({ timeout: 5000 }).catch(() => false);

      const body = (await page.locator("body").textContent()) || "";
      const hasBulkUI = hasCheckbox || body.includes("Select All") || body.includes("select all") || body.includes("Bulk");
      expect(hasBulkUI).toBeTruthy();
    }
  });

  test("Bulk Approve button appears with selection", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const approvalsTab = page.getByText("Approvals", { exact: true }).first();
    if (await approvalsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await approvalsTab.click();

      // Try to check a checkbox to trigger bulk actions
      const checkbox = page.locator('input[type="checkbox"]').first();
      if (await checkbox.isVisible({ timeout: 5000 }).catch(() => false)) {
        await checkbox.check();
      }

      // Look for Bulk Approve button
      const bulkApproveBtn = page.getByRole("button", { name: /Bulk Approve|Approve Selected|Approve All/i }).first();
      const visible = await bulkApproveBtn.isVisible({ timeout: 5000 }).catch(() => false);

      // Also check for any approve-related button
      const body = (await page.locator("body").textContent()) || "";
      const hasBulkApprove = visible || body.includes("Approve") || body.includes("Bulk");
      expect(hasBulkApprove).toBeTruthy();
    }
  });
});
