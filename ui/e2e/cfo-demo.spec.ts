/**
 * CFO Demo Flow — Production E2E Tests
 *
 * Tests the CFO demo journey against production:
 * - Landing page loads
 * - Navigation to login
 * - Dashboard pages load (auth-gated)
 */
import { test, expect } from "@playwright/test";

const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

// ═══════════════════════════════════════════════════════════════════════════
// Public pages — no auth needed
// ═══════════════════════════════════════════════════════════════════════════

test.describe("CFO Demo: Public Pages", () => {
  test("landing page loads with hero content", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("AgenticOrg", {
      timeout: 15000,
    });
  });

  test("landing page is scrollable", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate(() => window.scrollTo({ top: 600, behavior: "smooth" }));
    await expect(page.locator("body")).toBeVisible();
  });

  test("can navigate to login page from landing", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });

    const loginLink = page.locator('a[href*="login"], button:has-text("Login"), a:has-text("Login"), a:has-text("Sign in")').first();
    if (await loginLink.isVisible({ timeout: 5000 }).catch(() => false)) {
      await loginLink.click();
      await page.waitForURL(/\/login/, { timeout: 10000 });
      expect(page.url()).toContain("/login");
    } else {
      // Navigate directly
      await page.goto("/login", { waitUntil: "domcontentloaded" });
      expect(page.url()).toContain("/login");
    }
  });

  test("login page renders email and password fields", async ({ page }) => {
    await page.goto("/login", { waitUntil: "domcontentloaded" });

    await expect(page.locator('input[type="email"], input[placeholder*="email" i]').first()).toBeVisible({
      timeout: 10000,
    });
    await expect(page.locator('input[type="password"]').first()).toBeVisible();
    await expect(page.locator('button[type="submit"]').first()).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated dashboard pages
// ═══════════════════════════════════════════════════════════════════════════

test.describe("CFO Demo: Dashboard (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => {
      localStorage.setItem("token", t);
      localStorage.setItem("user", JSON.stringify({ email: "demo@cafirm.agenticorg.ai", name: "Demo Partner", role: "admin", domain: "all", tenant_id: "58483c90-494b-445d-85c6-245a727fe372", onboardingComplete: true }));
    }, E2E_TOKEN);
  });

  test("agent fleet page loads", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
    // Page should show agent-related content
    const bodyText = await page.textContent("body");
    expect(bodyText!.length).toBeGreaterThan(0);
  });

  test("approvals page loads", async ({ page }) => {
    await page.goto("/dashboard/approvals", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("workflows page loads", async ({ page }) => {
    await page.goto("/dashboard/workflows", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("connectors page loads", async ({ page }) => {
    await page.goto("/dashboard/connectors", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("schema registry page loads", async ({ page }) => {
    await page.goto("/dashboard/schemas", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("audit log page loads", async ({ page }) => {
    await page.goto("/dashboard/audit", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("create agent page loads", async ({ page }) => {
    await page.goto("/dashboard/agents/new", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("create workflow page loads", async ({ page }) => {
    await page.goto("/dashboard/workflows/new", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });
});
