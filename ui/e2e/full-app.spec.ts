/**
 * Full App E2E — Production Navigation & Page Integrity
 * Tests run against BASE_URL (default: https://app.agenticorg.ai)
 * No page.route() mocking — all responses are real.
 */
import { test, expect, Page } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const MARKETING = "https://agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;
function requireAuth(): void {
  if (!canAuth) throw new Error(
    "E2E_TOKEN is required for this spec. Set the E2E_TOKEN env var — the suite runs against production and must have credentials.",
  );
}


async function authenticate(page: Page) {
  await page.goto(`${APP}/login`);
  await setSessionToken(page, E2E_TOKEN);
}

// ---------------------------------------------------------------------------
// Public pages — no auth required
// ---------------------------------------------------------------------------

test.describe("Full App — Public Pages", () => {
  test("landing page loads with correct branding", async ({ page }) => {
    const response = await page.goto(APP, { waitUntil: "networkidle" });
    expect(response?.status()).toBeLessThan(400);
    await expect(page.getByText("AgenticOrg").first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/agent/i).first()).toBeVisible({ timeout: 10000 });
  });

  test("login page renders form", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await expect(page.locator('input[type="email"]')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test("pricing page loads", async ({ page }) => {
    const response = await page.goto(`${APP}/pricing`, { waitUntil: "networkidle" });
    expect(response?.status()).toBeLessThan(400);
    await expect(page.getByText("Pricing").first()).toBeVisible({ timeout: 10000 });
  });

  test("blog page loads", async ({ page }) => {
    const response = await page.goto(`${APP}/blog`, { waitUntil: "networkidle" });
    expect(response?.status()).toBeLessThan(400);
    await expect(page.getByText("Blog").first()).toBeVisible({ timeout: 10000 });
  });

  test("playground page loads", async ({ page }) => {
    const response = await page.goto(`${APP}/playground`, { waitUntil: "networkidle" });
    expect(response?.status()).toBeLessThan(400);
    await expect(page.getByText("Playground").first()).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// Dashboard pages — auth required, every page + interactive element
// ---------------------------------------------------------------------------

test.describe("Full App — Dashboard (Auth Required)", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({ page }) => {
    requireAuth();
    await authenticate(page);
  });

  // --- Dashboard ---
  test("Dashboard loads with metric cards", async ({ page }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    await expect(page.getByText("Dashboard").first()).toBeVisible({ timeout: 10000 });
    // Should show some metric label
    const mainText = await page.locator("main").textContent() || "";
    expect(mainText).not.toContain("NaN");
    expect(mainText).not.toContain("undefined");
  });

  // --- Agents ---
  test("Agents page shows agent fleet", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents`, { waitUntil: "networkidle" });
    await expect(page.getByText("Agent").first()).toBeVisible({ timeout: 10000 });
    const mainText = await page.locator("main").textContent() || "";
    expect(mainText).not.toContain("NaN");
  });

  test("Agent detail page loads when clicking an agent", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents`, { waitUntil: "networkidle" });
    const firstCard = page.locator('[class*="card"], [class*="Card"]').first();
    if (await firstCard.isVisible({ timeout: 5000 }).catch(() => false)) {
      await firstCard.click();
      await page.waitForLoadState("networkidle");
      await expect(page.locator("text=Page not found")).not.toBeVisible();
    }
  });

  test("Create Agent page loads form", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents/new`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(
      page.locator('input, select, [role="combobox"]').first()
    ).toBeVisible({ timeout: 10000 });
  });

  // --- Workflows ---
  test("Workflows page shows workflow list", async ({ page }) => {
    await page.goto(`${APP}/dashboard/workflows`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.getByText("Workflow").first()).toBeVisible({ timeout: 10000 });
  });

  test("Create Workflow page loads form", async ({ page }) => {
    await page.goto(`${APP}/dashboard/workflows/new`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(
      page.locator('input, select, [role="combobox"]').first()
    ).toBeVisible({ timeout: 10000 });
  });

  // --- Approvals ---
  test("Approvals page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/approvals`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.getByText("Approval").first()).toBeVisible({ timeout: 10000 });
  });

  // --- Connectors ---
  test("Connectors page shows connector list", async ({ page }) => {
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.getByText("Connector").first()).toBeVisible({ timeout: 10000 });
  });

  test("Create Connector page loads form", async ({ page }) => {
    await page.goto(`${APP}/dashboard/connectors/new`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(
      page.locator('input, select, [role="combobox"]').first()
    ).toBeVisible({ timeout: 10000 });
  });

  // --- Schemas ---
  test("Schemas page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/schemas`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.getByText("Schema").first()).toBeVisible({ timeout: 10000 });
  });

  // --- Audit ---
  test("Audit page shows table", async ({ page }) => {
    await page.goto(`${APP}/dashboard/audit`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(
      page.locator("table, [class*='border']").first()
    ).toBeVisible({ timeout: 10000 });
  });

  // --- Settings ---
  test("Settings page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/settings`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.getByText("Settings").first()).toBeVisible({ timeout: 10000 });
  });

  // --- Full Navigation ---
  test("All sidebar links navigate without 404", async ({ page }) => {
    const paths = [
      "/dashboard",
      "/dashboard/agents",
      "/dashboard/workflows",
      "/dashboard/approvals",
      "/dashboard/connectors",
      "/dashboard/schemas",
      "/dashboard/audit",
      "/dashboard/settings",
    ];
    for (const path of paths) {
      await page.goto(`${APP}${path}`, { waitUntil: "networkidle" });
      const notFound = await page
        .locator("text=Page not found")
        .isVisible()
        .catch(() => false);
      expect(notFound, `${path} shows 404`).toBe(false);
      // No NaN in content
      const mainText = await page.locator("main").textContent() || "";
      expect(mainText, `NaN on ${path}`).not.toContain("NaN");
    }
  });
});
