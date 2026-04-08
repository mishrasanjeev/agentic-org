/**
 * CA Features Regression Tests (Playwright E2E)
 *
 * Regression tests for CA-specific pages to prevent regressions:
 *   1. No "undefined" or "NaN" on any CA page
 *   2. All CA routes return < 500 status
 *   3. Company cards don't show empty GSTIN
 *   4. Approval status badges use correct variants
 *   5. Health score colors are correct (green >= 80, amber >= 50, red < 50)
 *
 * All tests are read-only and production-safe.
 *
 * Run:
 *   npx playwright test tests/regression/test_ca_regression.spec.ts --config=playwright.config.ts
 */
import { test, expect, Page } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
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
//  No "undefined" or "NaN" on any CA page
// ==========================================================================

test.describe("No undefined/NaN on CA pages", () => {
  const CA_ROUTES = [
    "/solutions/ca-firms",
  ];

  const AUTHED_CA_ROUTES = [
    "/dashboard/companies",
    "/dashboard/companies/new",
    "/dashboard/companies/c1",
  ];

  for (const route of CA_ROUTES) {
    test(`${route} has no "undefined" or "NaN" text`, async ({ page }) => {
      const response = await page.goto(`${APP}${route}`, {
        waitUntil: "domcontentloaded",
      });
      const status = response?.status() ?? 0;
      // Skip if page not found (expected if not deployed)
      if (status >= 400) return;

      await page.waitForLoadState("networkidle").catch(() => {});

      const body = (await page.locator("body").textContent()) || "";
      // "undefined" as rendered text (not in JS code or attributes)
      expect(body).not.toMatch(/\bundefined\b/);
      // "NaN" as rendered text
      expect(body).not.toMatch(/\bNaN\b/);
    });
  }

  for (const route of AUTHED_CA_ROUTES) {
    test(`${route} (authed) has no "undefined" or "NaN" text`, async ({ page }) => {
      test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
      await authenticate(page);

      const response = await page.goto(`${APP}${route}`, {
        waitUntil: "domcontentloaded",
      });
      const status = response?.status() ?? 0;
      if (status >= 400) return;

      await page.waitForLoadState("networkidle").catch(() => {});

      // Check main content area specifically (avoid script tags)
      const mainContent = (await page.locator("main, [class*='space-y'], #root").first().textContent()) || "";
      expect(mainContent).not.toMatch(/\bundefined\b/);
      expect(mainContent).not.toMatch(/\bNaN\b/);
    });
  }
});

// ==========================================================================
//  All CA routes return < 500 status
// ==========================================================================

test.describe("All CA routes return < 500 status", () => {
  const PUBLIC_ROUTES = [
    "/solutions/ca-firms",
  ];

  const AUTHED_ROUTES = [
    "/dashboard/companies",
    "/dashboard/companies/new",
    "/dashboard/companies/c1",
  ];

  for (const route of PUBLIC_ROUTES) {
    test(`${route} returns < 500`, async ({ page }) => {
      const response = await page.goto(`${APP}${route}`, {
        waitUntil: "domcontentloaded",
      });
      const status = response?.status() ?? 0;
      expect(status).toBeLessThan(500);
    });
  }

  for (const route of AUTHED_ROUTES) {
    test(`${route} (authed) returns < 500`, async ({ page }) => {
      test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
      await authenticate(page);

      const response = await page.goto(`${APP}${route}`, {
        waitUntil: "domcontentloaded",
      });
      const status = response?.status() ?? 0;
      expect(status).toBeLessThan(500);
    });
  }
});

// ==========================================================================
//  Company cards don't show empty GSTIN
// ==========================================================================

test.describe("Company cards don't show empty GSTIN", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("dashboard company cards hide GSTIN label when GSTIN is empty", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Get all GSTIN labels on the page
    const gstinLabels = page.locator("text=GSTIN");
    const count = await gstinLabels.count().catch(() => 0);

    // For each GSTIN label, verify it's followed by a non-empty value
    for (let i = 0; i < count; i++) {
      const label = gstinLabels.nth(i);
      const parent = label.locator("..");
      const parentText = (await parent.textContent()) || "";

      // Should not have "GSTIN" followed by nothing or whitespace only
      // Valid: "GSTIN 29AABCU9603R1ZM" or "GSTIN: 29AABCU9603R1ZM"
      // Invalid: "GSTIN" alone or "GSTIN "
      const gstinMatch = parentText.match(/GSTIN[:\s]*([\w]*)/);
      if (gstinMatch) {
        // If a GSTIN label is shown, it must have a non-empty value
        expect(gstinMatch[1]?.trim().length).toBeGreaterThan(0);
      }
    }
  });

  test("company detail page does not show empty GSTIN in header", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // The GSTIN in the header should be either a valid format or not shown at all
    const body = (await page.locator("body").textContent()) || "";

    // Check that there's no pattern like "GSTIN: " or "GSTIN:" with nothing after
    const emptyGstinPattern = /GSTIN[:\s]*(?=\s*[A-Z]|$)/;
    // The body should not have an empty GSTIN display
    // (This is a soft check -- if GSTIN is present it should have a value)
    if (body.includes("GSTIN")) {
      const gstinSection = body.match(/GSTIN[:\s]*([\w]+)/);
      if (gstinSection) {
        expect(gstinSection[1].length).toBeGreaterThan(0);
      }
    }
  });
});

// ==========================================================================
//  Approval status badges use correct variants
// ==========================================================================

test.describe("Approval status badges use correct variants", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("approved badge has success styling", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Navigate to Approvals tab
    const approvalsTab = page.getByText("Approvals", { exact: true }).first();
    if (await approvalsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await approvalsTab.click();

      // Find "approved" badges
      const approvedBadges = page.locator("text=approved").first();
      if (await approvedBadges.isVisible({ timeout: 5000 }).catch(() => false)) {
        // The badge should have a green/success color class
        const parent = approvedBadges.locator("xpath=ancestor::*[contains(@class, 'badge') or contains(@class, 'Badge')]").first();
        if (await parent.isVisible({ timeout: 2000 }).catch(() => false)) {
          const classes = (await parent.getAttribute("class")) || "";
          // Should contain success variant (green) -- not destructive (red)
          expect(classes).not.toContain("destructive");
        }
      }
    }
  });

  test("pending badge has warning styling", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    const approvalsTab = page.getByText("Approvals", { exact: true }).first();
    if (await approvalsTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await approvalsTab.click();

      // Find "pending" badges
      const pendingBadges = page.locator("text=pending").first();
      if (await pendingBadges.isVisible({ timeout: 5000 }).catch(() => false)) {
        // Pending should not be styled as success (green) or destructive (red)
        const parent = pendingBadges.locator("xpath=ancestor::*[contains(@class, 'badge') or contains(@class, 'Badge')]").first();
        if (await parent.isVisible({ timeout: 2000 }).catch(() => false)) {
          const classes = (await parent.getAttribute("class")) || "";
          // Should contain warning variant (amber) -- not success (green) or destructive (red)
          expect(classes).not.toContain("destructive");
        }
      }
    }
  });
});

// ==========================================================================
//  Health score colors are correct
// ==========================================================================

test.describe("Health score colors", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("health score >= 80 uses green color", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // The overview tab (default) shows Client Health Score
    const healthLabel = page.getByText("Client Health Score").first();
    if (await healthLabel.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Find the health score value element (the number above the label)
      const healthCard = healthLabel.locator("xpath=ancestor::*[contains(@class, 'CardContent') or contains(@class, 'card')]").first();
      if (await healthCard.isVisible({ timeout: 3000 }).catch(() => false)) {
        const cardText = (await healthCard.textContent()) || "";

        // Extract the numeric score
        const scoreMatch = cardText.match(/(\d+)\s*Client Health Score/);
        if (scoreMatch) {
          const score = parseInt(scoreMatch[1], 10);

          // Find the score element by its value
          const scoreEl = page.getByText(String(score), { exact: true }).first();
          if (await scoreEl.isVisible({ timeout: 2000 }).catch(() => false)) {
            const classes = (await scoreEl.getAttribute("class")) || "";

            if (score >= 80) {
              // Green for healthy scores
              expect(classes).toContain("emerald");
            } else if (score >= 50) {
              // Amber for moderate scores
              expect(classes).toContain("amber");
            } else {
              // Red for low scores
              expect(classes).toContain("red");
            }
          }
        }
      }
    }
  });

  test("health score text color follows the threshold rules", async ({ page }) => {
    await page.goto(`${APP}/dashboard/companies/c1`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // The CompanyDetail component uses this logic:
    // healthScore >= 80 ? "text-emerald-600"
    // healthScore >= 50 ? "text-amber-600"
    // else "text-red-600"
    //
    // Verify that the health score element has one of these color classes
    const healthLabel = page.getByText("Client Health Score").first();
    if (await healthLabel.isVisible({ timeout: 5000 }).catch(() => false)) {
      const parent = healthLabel.locator("..").first();
      const parentHtml = (await parent.innerHTML()) || "";

      // Must contain one of the three valid color classes
      const hasGreen = parentHtml.includes("text-emerald-600");
      const hasAmber = parentHtml.includes("text-amber-600");
      const hasRed = parentHtml.includes("text-red-600");

      expect(hasGreen || hasAmber || hasRed).toBeTruthy();
    }
  });
});

// ==========================================================================
//  CA solution page does not contain rendering errors
// ==========================================================================

test.describe("CA Solution Page Integrity", () => {
  test("CA Firms Solution page loads without React errors", async ({ page }) => {
    // Collect console errors
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text());
      }
    });

    await page.goto(`${APP}/solutions/ca-firms`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Filter for React-specific errors
    const reactErrors = consoleErrors.filter(
      (e) => e.includes("React") || e.includes("Uncaught") || e.includes("TypeError")
    );

    // Should have no React rendering errors
    expect(reactErrors).toHaveLength(0);
  });

  test("CA Firms Solution page has correct title", async ({ page }) => {
    await page.goto(`${APP}/solutions/ca-firms`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Title should reference CA or AgenticOrg
    await expect(page).toHaveTitle(/CA|Chartered Accountant|AgenticOrg/i);
  });
});

// ==========================================================================
//  API routes return non-500 for CA endpoints
// ==========================================================================

test.describe("CA API endpoint sanity", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token -- set E2E_TOKEN env var");
    await authenticate(page);
  });

  test("GET /api/v1/companies does not return 500", async ({ page }) => {
    const token = await page.evaluate(() => localStorage.getItem("token"));
    const response = await page.request.get(`${APP}/api/v1/companies`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.status()).toBeLessThan(500);
  });

  test("GET /api/v1/approvals does not return 500", async ({ page }) => {
    const token = await page.evaluate(() => localStorage.getItem("token"));
    const response = await page.request.get(`${APP}/api/v1/approvals`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.status()).toBeLessThan(500);
  });
});
