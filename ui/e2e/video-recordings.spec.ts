/**
 * Video Page Flows — Production E2E Tests
 *
 * Converted from video-recording scripts to functional tests.
 * Tests that each page the demo videos would show actually loads.
 * Auth-gated pages fail when E2E_TOKEN is not set, and each one must render
 * the signed-in layout: a page that bounced to /login is not "loaded".
 */
import { expect, test } from "./helpers/test";
import { E2E_TOKEN, expectSignedIn, setSessionToken } from "./helpers/auth";

const canAuth = !!E2E_TOKEN;
function requireAuth(): void {
  if (!canAuth) throw new Error(
    "E2E_TOKEN is required for this spec. Set the E2E_TOKEN env var — the suite runs against production and must have credentials.",
  );
}


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
    requireAuth();
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await setSessionToken(page, E2E_TOKEN);
  });

  test("dashboard loads", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });

  test("observatory page loads", async ({ page }) => {
    await page.goto("/dashboard/observatory", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });

  test("approvals page loads", async ({ page }) => {
    await page.goto("/dashboard/approvals", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });

  test("connectors page loads", async ({ page }) => {
    await page.goto("/dashboard/connectors", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });

  test("workflows page loads", async ({ page }) => {
    await page.goto("/dashboard/workflows", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });

  test("audit page loads", async ({ page }) => {
    await page.goto("/dashboard/audit", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated: CFO Finance pages (Video 2)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: CFO Finance (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await setSessionToken(page, E2E_TOKEN);
  });

  test("agents page loads with agent content", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });

  test("observatory page loads for finance tracing", async ({ page }) => {
    await page.goto("/dashboard/observatory", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated: CHRO HR pages (Video 3)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: CHRO HR (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await setSessionToken(page, E2E_TOKEN);
  });

  test("agents page loads", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });

  test("audit page loads", async ({ page }) => {
    await page.goto("/dashboard/audit", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated: CMO Marketing pages (Video 4)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: CMO Marketing (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await setSessionToken(page, E2E_TOKEN);
  });

  test("agents page loads for marketing agents", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Auth-gated: COO Operations pages (Video 5)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Video Flows: COO Operations (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await setSessionToken(page, E2E_TOKEN);
  });

  test("agents page loads for ops agents", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });

  test("connectors page loads", async ({ page }) => {
    await page.goto("/dashboard/connectors", { waitUntil: "domcontentloaded" });
    await expectSignedIn(page);
  });
});
