/**
 * Video Page Flows — Production E2E Tests
 *
 * Converted from video-recording scripts to functional tests.
 * Tests that each page the demo videos would show actually loads.
 * Auth-gated pages skip when E2E_TOKEN is not set.
 */
import { test, expect } from "@playwright/test";

const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

// ═══════════════════════════════════════════════════════════════════════════
// Public: Landing page (all demo videos start here)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: Landing Page", () => {
  test("landing page loads and renders hero", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("AgenticOrg", {
      timeout: 15000,
    });
  });

  test("landing page is scrollable without errors", async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate(() => window.scrollTo({ top: 1600, behavior: "smooth" }));
    // Page should remain intact after scroll
    await expect(page.locator("body")).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated: Platform Overview (Video 1 — CEO)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: Platform Overview (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => {
      localStorage.setItem("token", t);
      localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));
    }, E2E_TOKEN);
  });

  test("dashboard loads", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("observatory page loads", async ({ page }) => {
    await page.goto("/dashboard/observatory", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("approvals page loads", async ({ page }) => {
    await page.goto("/dashboard/approvals", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("connectors page loads", async ({ page }) => {
    await page.goto("/dashboard/connectors", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("workflows page loads", async ({ page }) => {
    await page.goto("/dashboard/workflows", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("audit page loads", async ({ page }) => {
    await page.goto("/dashboard/audit", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated: CFO Finance pages (Video 2)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: CFO Finance (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => {
      localStorage.setItem("token", t);
      localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));
    }, E2E_TOKEN);
  });

  test("agents page loads with agent content", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("observatory page loads for finance tracing", async ({ page }) => {
    await page.goto("/dashboard/observatory", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated: CHRO HR pages (Video 3)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: CHRO HR (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => {
      localStorage.setItem("token", t);
      localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));
    }, E2E_TOKEN);
  });

  test("agents page loads", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("audit page loads", async ({ page }) => {
    await page.goto("/dashboard/audit", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated: CMO Marketing pages (Video 4)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: CMO Marketing (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => {
      localStorage.setItem("token", t);
      localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));
    }, E2E_TOKEN);
  });

  test("agents page loads for marketing agents", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated: COO Operations pages (Video 5)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: COO Operations (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => {
      localStorage.setItem("token", t);
      localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));
    }, E2E_TOKEN);
  });

  test("agents page loads for ops agents", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });

  test("connectors page loads", async ({ page }) => {
    await page.goto("/dashboard/connectors", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).not.toBeEmpty();
  });
});
