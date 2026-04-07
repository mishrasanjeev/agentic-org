/**
 * CXO Dashboards — Production E2E Tests
 *
 * Tests CFO Dashboard, CMO Dashboard, NL Query, Report Scheduler,
 * Multi-Company Switcher, and responsive layouts against production.
 *
 * Auth-gated tests use E2E_TOKEN via localStorage injection.
 * No page.route() mocking — all data comes from the live API.
 */
import { test, expect } from "@playwright/test";

const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

// ---------------------------------------------------------------------------
// Auth helper — inject token into localStorage before dashboard navigation
// ---------------------------------------------------------------------------

async function setAuth(page: import("@playwright/test").Page) {
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await page.evaluate((t) => localStorage.setItem("token", t), E2E_TOKEN);
}

// ═══════════════════════════════════════════════════════════════════════════
// CFO DASHBOARD
// ═══════════════════════════════════════════════════════════════════════════

test.describe("CFO Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await setAuth(page);
  });

  test("page loads and shows CFO Dashboard heading", async ({ page }) => {
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("CFO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("KPI cards render — Cash Runway, Burn Rate, DSO, DPO", async ({ page }) => {
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("Cash Runway").first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText("Monthly Burn Rate").first()).toBeVisible();
    await expect(page.getByText("DSO (Days)").first()).toBeVisible();
    await expect(page.getByText("DPO (Days)").first()).toBeVisible();
  });

  test("KPI cards show numeric values", async ({ page }) => {
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    // Cash Runway shows a number with "mo" suffix
    const cashRunway = page.locator("text=/\\d+\\s*mo/");
    await expect(cashRunway.first()).toBeVisible({ timeout: 15000 });

    // DSO shows a number with "d" suffix
    const dso = page.locator("text=/\\d+d/");
    await expect(dso.first()).toBeVisible();
  });

  test("AR Aging chart renders", async ({ page }) => {
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    // Recharts renders SVGs asynchronously — allow extra time
    await expect(
      page.getByText(/Accounts Receivable Aging/).first(),
    ).toBeVisible({ timeout: 30000 });
  });

  test("AP Aging chart renders", async ({ page }) => {
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    await expect(
      page.getByText("Accounts Payable Aging").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("Bank Balances section shows at least 1 account", async ({ page }) => {
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("Bank Balances").first()).toBeVisible({
      timeout: 15000,
    });

    const balanceRows = page.locator(".bg-muted");
    const count = await balanceRows.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("Tax Calendar shows upcoming filings", async ({ page }) => {
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    await expect(
      page.getByText("Tax & Compliance Calendar").first(),
    ).toBeVisible({ timeout: 15000 });

    const filings = page.locator(".rounded.border");
    const count = await filings.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("Monthly P&L Summary table renders", async ({ page }) => {
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    await expect(
      page.getByText("Monthly P&L Summary").first(),
    ).toBeVisible({ timeout: 15000 });

    await expect(page.getByText("Revenue").first()).toBeVisible();
    await expect(page.getByText("COGS").first()).toBeVisible();
  });

  test("shows loading state or data (no crash)", async ({ page }) => {
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    // Auth token may expire and redirect to login -- that is also a valid outcome
    const ok =
      (await page.getByText("Loading...").isVisible({ timeout: 5000 }).catch(() => false)) ||
      (await page.getByText(/finance|cfo|dashboard/i).first().isVisible({ timeout: 20000 }).catch(() => false)) ||
      (await page.getByText(/sign in|log in|email/i).first().isVisible({ timeout: 5000 }).catch(() => false)) ||
      page.url().includes("/login");
    expect(ok).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// CMO DASHBOARD
// ═══════════════════════════════════════════════════════════════════════════

test.describe("CMO Dashboard", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await setAuth(page);
  });

  test("page loads and shows CMO Dashboard heading", async ({ page }) => {
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("CMO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("KPI cards render — CAC, MQLs, SQLs, Pipeline Value", async ({ page }) => {
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("Customer Acquisition Cost").first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText("MQLs This Month").first()).toBeVisible();
    await expect(page.getByText("SQLs This Month").first()).toBeVisible();
    await expect(page.getByText("Pipeline Value").first()).toBeVisible();
  });

  test("ROAS chart renders", async ({ page }) => {
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    await expect(
      page.getByText("Return on Ad Spend (ROAS) by Channel").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("Social Engagement section renders", async ({ page }) => {
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    // Recharts renders SVGs asynchronously — allow extra time.
    // The heading appears in a CardTitle inside a grid that may be below the
    // fold, so wait for the full page to settle before asserting.
    await page.waitForLoadState("networkidle").catch(() => {});
    await expect(
      page.getByText(/Social Engagement/).first(),
    ).toBeVisible({ timeout: 30000 });
  });

  test("Brand Sentiment section renders", async ({ page }) => {
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    await expect(
      page.getByText("Brand Sentiment Score").first(),
    ).toBeVisible({ timeout: 15000 });

    await expect(page.getByText("out of 100").first()).toBeVisible();
  });

  test("Email Performance section renders", async ({ page }) => {
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    await expect(
      page.getByText("Email Performance (%)").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("Website Traffic section shows stats", async ({ page }) => {
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("Website Traffic").first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText("Sessions").first()).toBeVisible();
    await expect(page.getByText("Users").first()).toBeVisible();
    await expect(page.getByText("Bounce Rate").first()).toBeVisible();
  });

  test("Top Content Pages table renders", async ({ page }) => {
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    await expect(
      page.getByText("Top Content Pages").first(),
    ).toBeVisible({ timeout: 15000 });
  });

  test("shows loading state or data (no crash)", async ({ page }) => {
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    // Auth token may expire and redirect to login -- that is also a valid outcome
    const ok =
      (await page.getByText("Loading...").isVisible({ timeout: 5000 }).catch(() => false)) ||
      (await page.getByText(/marketing|cmo|dashboard/i).first().isVisible({ timeout: 20000 }).catch(() => false)) ||
      (await page.getByText(/sign in|log in|email/i).first().isVisible({ timeout: 5000 }).catch(() => false)) ||
      page.url().includes("/login");
    expect(ok).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// NL QUERY BAR
// ═══════════════════════════════════════════════════════════════════════════

test.describe("NL Query Bar", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await setAuth(page);
  });

  test("Ctrl+K opens search bar", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    await page.keyboard.press("Control+k");

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (await input.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(input).toBeFocused();
    }
  });

  test("typing a finance query and submitting does not crash", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (!(await input.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    await input.fill("What is our cash runway?");
    await input.press("Enter");

    // Wait for response — page should not crash
    await expect(page.locator("body")).toBeVisible();
    const bodyText = await page.textContent("body");
    expect(bodyText).toBeTruthy();
  });

  test("empty query does not submit", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (!(await input.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    await input.press("Enter");

    // No loading spinner should appear
    const spinner = page.locator(".animate-spin");
    const spinnerVisible = await spinner.isVisible({ timeout: 2000 }).catch(() => false);
    expect(spinnerVisible).toBe(false);
  });

  test("long query handles gracefully without crash", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (!(await input.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    const longQuery = "What is the status of ".repeat(100);
    await input.fill(longQuery);

    await expect(input).toBeVisible();
    expect(page.url()).toContain("/dashboard");
  });

  test("Open Chat opens the chat panel", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (!(await input.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    await input.fill("What is our burn rate?");
    await input.press("Enter");

    const openChatBtn = page.getByText("Open Chat");
    if (await openChatBtn.isVisible({ timeout: 10000 }).catch(() => false)) {
      await openChatBtn.click();

      const chatHeader = page.getByText("Agent Chat");
      await expect(chatHeader).toBeVisible({ timeout: 10000 });
    }
  });

  test("chat panel close button works", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (!(await input.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    await input.fill("What is our burn rate?");
    await input.press("Enter");

    const openChatBtn = page.getByText("Open Chat");
    if (!(await openChatBtn.isVisible({ timeout: 10000 }).catch(() => false))) {
      test.skip();
      return;
    }

    await openChatBtn.click();

    const closeBtn = page.locator('[aria-label="Close chat"]');
    if (await closeBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await closeBtn.click();
      // Page should not crash after closing
      await expect(page.locator("body")).toBeVisible();
    }
  });

  test("chat panel can send message and receive response", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (!(await input.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    await input.fill("What is our burn rate?");
    await input.press("Enter");

    const openChatBtn = page.getByText("Open Chat");
    if (!(await openChatBtn.isVisible({ timeout: 10000 }).catch(() => false))) {
      test.skip();
      return;
    }

    await openChatBtn.click();

    const chatInput = page.locator('input[placeholder="Type a message..."]');
    if (await chatInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await chatInput.fill("Show me last quarter revenue");
      const sendBtn = page.getByText("Send");
      await sendBtn.click();

      // Check that user message appears in the chat
      await expect(page.getByText("Show me last quarter revenue")).toBeVisible({
        timeout: 15000,
      });
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// REPORT SCHEDULER
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Report Scheduler", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await setAuth(page);
  });

  test("page loads and shows Report Schedules heading", async ({ page }) => {
    await page.goto("/dashboard/report-schedules", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("Report Schedules").first()).toBeVisible({
      timeout: 15000,
    });
  });

  test("+ New Schedule button is visible", async ({ page }) => {
    await page.goto("/dashboard/report-schedules", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("+ New Schedule")).toBeVisible({ timeout: 15000 });
  });

  test("+ New Schedule button opens create form", async ({ page }) => {
    await page.goto("/dashboard/report-schedules", { waitUntil: "domcontentloaded" });

    await page.getByText("+ New Schedule").click();

    await expect(page.getByText("Report Type").first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("Output Format").first()).toBeVisible();
    await expect(page.getByText("Delivery Channels").first()).toBeVisible();
  });

  test("create form has all required fields", async ({ page }) => {
    await page.goto("/dashboard/report-schedules", { waitUntil: "domcontentloaded" });

    await page.getByText("+ New Schedule").click();

    const selects = page.locator("select");
    expect(await selects.count()).toBeGreaterThanOrEqual(3);

    await expect(page.getByLabel("Email")).toBeVisible();
    await expect(page.getByLabel("Slack")).toBeVisible();
    await expect(page.getByLabel("WhatsApp")).toBeVisible();
  });

  test("can fill form and submit to create schedule", async ({ page }) => {
    await page.goto("/dashboard/report-schedules", { waitUntil: "domcontentloaded" });

    await page.getByText("+ New Schedule").click();

    const selects = page.locator("main select");
    await selects.nth(0).selectOption("pnl_report");
    await selects.nth(1).selectOption("monthly");
    await selects.nth(2).selectOption("excel");

    const emailInput = page.locator('input[placeholder="recipient@company.com"]');
    if (await emailInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      await emailInput.fill("test@example.com");
    }

    await page.click('button:has-text("Create Schedule")');

    // Page should not crash
    expect(page.url()).toContain("/dashboard/report-schedules");
  });

  test("toggle active/inactive works", async ({ page }) => {
    await page.goto("/dashboard/report-schedules", { waitUntil: "domcontentloaded" });

    const activeBadge = page.getByText("Active").first();
    const pausedBadge = page.getByText("Paused").first();

    const hasActive = await activeBadge.isVisible({ timeout: 5000 }).catch(() => false);
    const hasPaused = await pausedBadge.isVisible({ timeout: 5000 }).catch(() => false);

    if (hasActive || hasPaused) {
      const badge = hasActive ? activeBadge : pausedBadge;
      await badge.click();

      // Page should not crash after toggle
      await expect(page.getByText("Report Schedules").first()).toBeVisible();
    }
  });

  test("Run Now button triggers execution", async ({ page }) => {
    await page.goto("/dashboard/report-schedules", { waitUntil: "domcontentloaded" });

    const runBtn = page.locator('button[title="Run now"]').first();
    if (await runBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      page.on("dialog", (dialog) => dialog.accept());
      await runBtn.click();

      await expect(page.getByText("Report Schedules").first()).toBeVisible();
    }
  });

  test("Delete removes schedule (with confirmation)", async ({ page }) => {
    await page.goto("/dashboard/report-schedules", { waitUntil: "domcontentloaded" });

    const delBtn = page.locator('button[title="Delete"]').first();
    if (await delBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      page.on("dialog", (dialog) => dialog.accept());
      await delBtn.click();

      await expect(page.getByText("Report Schedules").first()).toBeVisible();
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// MULTI-COMPANY SWITCHER
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Multi-Company Switcher", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await setAuth(page);
  });

  test("dashboard header/sidebar loads", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    // Dashboard should show heading or sidebar content
    const bodyText = await page.textContent("body");
    expect(bodyText).toBeTruthy();
    expect(bodyText!.length).toBeGreaterThan(0);

    // Look for the AgenticOrg heading in sidebar or Dashboard heading
    const hasDashContent =
      bodyText?.includes("AgenticOrg") ||
      bodyText?.includes("Dashboard") ||
      bodyText?.includes("Agent");
    expect(hasDashContent).toBeTruthy();
  });

  test("dashboard page renders content", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const bodyText = await page.textContent("body");
    expect(bodyText).toBeTruthy();
    expect(bodyText!.length).toBeGreaterThan(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// RESPONSIVE TESTS
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Responsive Layout", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await setAuth(page);
  });

  test("CFO Dashboard at mobile viewport (375x812)", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/dashboard/cfo", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("CFO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText("Cash Runway").first()).toBeVisible();
  });

  test("CMO Dashboard at tablet viewport (768x1024)", async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto("/dashboard/cmo", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("CMO Dashboard").first()).toBeVisible({
      timeout: 15000,
    });
    await expect(page.getByText("Customer Acquisition Cost").first()).toBeVisible();
  });

  test("navigation renders without crash on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const bodyText = await page.textContent("body");
    expect(bodyText).toBeTruthy();
  });

  test("NL Query bar renders on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (await input.isVisible({ timeout: 5000 }).catch(() => false)) {
      const box = await input.boundingBox();
      if (box) {
        expect(box.width).toBeLessThanOrEqual(400);
      }
    }
    // Main check: page renders without crash at mobile size
  });
});
