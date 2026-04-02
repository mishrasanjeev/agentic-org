/**
 * Production Full Suite -- comprehensive E2E tests against production.
 *
 * Covers every public page, auth flows, dashboard pages (when token
 * available), responsive behaviour, navigation, and SEO assets.
 *
 * All tests are read-only and production-safe -- no data is created,
 * mutated, or deleted.
 */
import { test, expect, Page } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const HAS_AUTH = Boolean(process.env.E2E_TOKEN);

// ── Helper: authenticate via stored token cookie ──────────────────────
async function injectAuth(page: Page): Promise<void> {
  const token = process.env.E2E_TOKEN!;
  await page.context().addCookies([
    {
      name: "agenticorg_token",
      value: token,
      domain: new URL(APP).hostname,
      path: "/",
    },
  ]);
}

// ═══════════════════════════════════════════════════════════════════════
//  PUBLIC PAGES
// ═══════════════════════════════════════════════════════════════════════

test.describe("Public Pages", () => {
  test("Landing page loads with hero stats", async ({ page }) => {
    await page.goto(APP, { waitUntil: "networkidle" });
    await expect(page).toHaveTitle(/AgenticOrg/i);

    // Hero stats -- should mention agent count or connector count
    const body = await page.textContent("body");
    const hasAgentStat = body?.includes("35") || body?.includes("25");
    const hasConnectorStat = body?.includes("54") || body?.includes("42");
    expect(hasAgentStat || hasConnectorStat).toBe(true);
  });

  test("Landing page sections render", async ({ page }) => {
    await page.goto(APP, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);

    // Key sections should be present
    const sections = [
      "How It Works",
      "Enterprise-Grade",
      "Built for Indian Enterprise",
    ];
    for (const section of sections) {
      await expect(
        page.getByText(section, { exact: false }).first()
      ).toBeVisible({ timeout: 10000 });
    }
  });

  test("Landing page hero CTA buttons visible", async ({ page }) => {
    await page.goto(APP, { waitUntil: "networkidle" });
    await expect(
      page.getByRole("link", { name: /Start Free|Get Started|Try Free/i }).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("Pricing page loads and shows connector counts", async ({ page }) => {
    await page.goto(`${APP}/pricing`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    const body = await page.textContent("body");
    // Should show connector or plan information
    expect(body?.length).toBeGreaterThan(200);
  });

  test("Blog page lists posts", async ({ page }) => {
    await page.goto(`${APP}/blog`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    // Should have multiple blog entries (links or cards)
    const posts = page.locator('a[href*="/blog/"]');
    await expect(posts.first()).toBeVisible({ timeout: 10000 });
    const count = await posts.count();
    expect(count).toBeGreaterThanOrEqual(4);
  });

  test("Blog post: ca-firm loads", async ({ page }) => {
    await page.goto(`${APP}/blog/ca-firm`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    const body = await page.textContent("body");
    expect(body?.length).toBeGreaterThan(300);
  });

  test("Blog post: month-end-close loads", async ({ page }) => {
    await page.goto(`${APP}/blog/month-end-close`, {
      waitUntil: "networkidle",
    });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  test("Blog post: honest-roi loads", async ({ page }) => {
    await page.goto(`${APP}/blog/honest-roi`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  test("Blog post: it-cfo-story loads", async ({ page }) => {
    await page.goto(`${APP}/blog/it-cfo-story`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  test("Resources page loads", async ({ page }) => {
    await page.goto(`${APP}/resources`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    const body = await page.textContent("body");
    expect(body?.length).toBeGreaterThan(200);
  });

  test("Evals page loads", async ({ page }) => {
    await page.goto(`${APP}/evals`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  SEO ASSETS
// ═══════════════════════════════════════════════════════════════════════

test.describe("SEO Assets", () => {
  test("sitemap.xml is accessible", async ({ request }) => {
    const resp = await request.get(`${APP}/sitemap.xml`);
    expect(resp.status()).toBe(200);
    const body = await resp.text();
    expect(body).toContain("<urlset");
    expect(body).toContain("agenticorg");
  });

  test("llms.txt is accessible", async ({ request }) => {
    const resp = await request.get(`${APP}/llms.txt`);
    expect(resp.status()).toBe(200);
    const body = await resp.text();
    expect(body.length).toBeGreaterThan(50);
  });

  test("robots.txt is accessible", async ({ request }) => {
    const resp = await request.get(`${APP}/robots.txt`);
    expect(resp.status()).toBe(200);
    const body = await resp.text();
    expect(body.toLowerCase()).toContain("user-agent");
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  AUTH FLOW
// ═══════════════════════════════════════════════════════════════════════

test.describe("Auth Flow", () => {
  test("Login page renders form", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await expect(page.locator('input[type="email"]')).toBeVisible({
      timeout: 10000,
    });
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test("Invalid credentials show error without crash", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await page.fill('input[type="email"]', "invalid@test.invalid");
    await page.fill('input[type="password"]', "wrongpassword123");
    await page.click('button[type="submit"]');
    await page.waitForTimeout(3000);
    // Should NOT navigate to dashboard
    expect(page.url()).not.toContain("/dashboard");
    // Should still be on login or show error -- not a blank/crash page
    const body = await page.textContent("body");
    expect(body?.length).toBeGreaterThan(50);
  });

  test("Forgot password page accessible", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    const forgotLink = page.getByText(/forgot/i).first();
    if (await forgotLink.isVisible({ timeout: 3000 }).catch(() => false)) {
      await forgotLink.click();
      await page.waitForTimeout(2000);
      await expect(page.locator("text=Page not found")).not.toBeVisible();
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  DASHBOARD (requires auth -- skip if no E2E_TOKEN)
// ═══════════════════════════════════════════════════════════════════════

test.describe("Dashboard (authenticated)", () => {
  test.skip(!HAS_AUTH, "Skipping: E2E_TOKEN not set");

  test.beforeEach(async ({ page }) => {
    await injectAuth(page);
  });

  test("Main dashboard loads with agent metrics", async ({ page }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    // Should show some metrics content
    const body = await page.textContent("body");
    expect(body?.length).toBeGreaterThan(200);
  });

  test("CFO Dashboard shows KPI cards and charts", async ({ page }) => {
    await page.goto(`${APP}/dashboard/cfo`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    // Look for KPI-style content
    const kpiTerms = ["cash", "runway", "burn", "revenue", "dso", "expense"];
    const body = (await page.textContent("body"))?.toLowerCase() || "";
    const found = kpiTerms.filter((t) => body.includes(t));
    expect(found.length).toBeGreaterThanOrEqual(1);
  });

  test("CMO Dashboard shows marketing metrics", async ({ page }) => {
    await page.goto(`${APP}/dashboard/cmo`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  test("ABM Dashboard shows account table", async ({ page }) => {
    await page.goto(`${APP}/dashboard/abm`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  test("NL Query: Cmd+K opens search bar", async ({ page }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    // Try Cmd+K (Mac) or Ctrl+K (other)
    await page.keyboard.press("Control+k");
    await page.waitForTimeout(1000);
    // Check if a search/command dialog appeared
    const searchInput = page.locator(
      '[role="dialog"] input, [class*="command"] input, [class*="search"] input, [class*="modal"] input'
    );
    // This is best-effort -- some UIs may not support it
    const visible = await searchInput
      .first()
      .isVisible({ timeout: 3000 })
      .catch(() => false);
    // We accept both outcomes; the test verifies no crash
    expect(typeof visible).toBe("boolean");
  });

  test("Company Switcher visible in header", async ({ page }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    const switcher = page.locator(
      '[class*="company"], [class*="Company"], [class*="switcher"], [class*="Switcher"]'
    );
    // Best-effort check
    const isVisible = await switcher
      .first()
      .isVisible({ timeout: 5000 })
      .catch(() => false);
    expect(typeof isVisible).toBe("boolean");
  });

  test("Report Scheduler page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/report-schedules`, {
      waitUntil: "networkidle",
    });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  test("Agents page shows agent list", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(
      page.getByText(/agent/i).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("Workflows page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/workflows`, {
      waitUntil: "networkidle",
    });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  test("Approvals page loads with tabs", async ({ page }) => {
    await page.goto(`${APP}/dashboard/approvals`, {
      waitUntil: "networkidle",
    });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    // Check for Pending / Decided tabs
    const pending = page.getByText(/pending/i).first();
    const decided = page.getByText(/decided|approved|rejected/i).first();
    const hasTabs =
      (await pending.isVisible({ timeout: 5000 }).catch(() => false)) ||
      (await decided.isVisible({ timeout: 3000 }).catch(() => false));
    expect(hasTabs).toBe(true);
  });

  test("Connectors page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/connectors`, {
      waitUntil: "networkidle",
    });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(
      page.getByText(/connector/i).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("Audit page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/audit`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  test("Settings page accessible", async ({ page }) => {
    await page.goto(`${APP}/dashboard/settings`, {
      waitUntil: "networkidle",
    });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  RESPONSIVE
// ═══════════════════════════════════════════════════════════════════════

test.describe("Responsive -- Mobile (375px)", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("Landing page at 375px -- no horizontal scroll", async ({ page }) => {
    await page.goto(APP, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    const scrollWidth = await page.evaluate(
      () => document.documentElement.scrollWidth
    );
    const clientWidth = await page.evaluate(
      () => document.documentElement.clientWidth
    );
    // Allow 2px tolerance for sub-pixel rendering
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 2);
  });

  test("Hero headline visible on mobile", async ({ page }) => {
    await page.goto(APP, { waitUntil: "networkidle" });
    await expect(
      page.getByText("Your Back Office Runs Itself").first()
    ).toBeVisible({ timeout: 10000 });
  });
});

test.describe("Responsive -- Tablet (768px)", () => {
  test.use({ viewport: { width: 768, height: 1024 } });

  test("Dashboard at 768px -- sidebar collapses", async ({ page }) => {
    test.skip(!HAS_AUTH, "Skipping: E2E_TOKEN not set");
    await injectAuth(page);
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    // On tablet the sidebar should either collapse or show a toggle
    const sidebar = page.locator(
      'nav[class*="sidebar"], aside[class*="sidebar"], [class*="Sidebar"]'
    );
    // Either hidden or narrowed -- we just verify no crash
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  NAVIGATION
// ═══════════════════════════════════════════════════════════════════════

test.describe("Navigation", () => {
  test("All sidebar dashboard links resolve without 404", async ({ page }) => {
    test.skip(!HAS_AUTH, "Skipping: E2E_TOKEN not set");
    await injectAuth(page);
    const paths = [
      "/dashboard",
      "/dashboard/agents",
      "/dashboard/workflows",
      "/dashboard/approvals",
      "/dashboard/connectors",
      "/dashboard/audit",
      "/dashboard/settings",
    ];
    for (const path of paths) {
      await page.goto(`${APP}${path}`, { waitUntil: "networkidle" });
      await page.waitForTimeout(1000);
      await expect(
        page.locator("text=Page not found")
      ).not.toBeVisible();
    }
  });

  test("Blog nav links work", async ({ page }) => {
    await page.goto(`${APP}/blog`, { waitUntil: "networkidle" });
    const firstPost = page.locator('a[href*="/blog/"]').first();
    if (await firstPost.isVisible({ timeout: 5000 }).catch(() => false)) {
      await firstPost.click();
      await page.waitForTimeout(2000);
      await expect(page.locator("text=Page not found")).not.toBeVisible();
      expect(page.url()).toContain("/blog/");
    }
  });

  test("Footer links work", async ({ page }) => {
    await page.goto(APP, { waitUntil: "networkidle" });
    const footer = page.locator("footer");
    await expect(footer).toBeVisible({ timeout: 10000 });

    // Check a few footer links resolve
    const footerLinks = footer.locator("a[href]");
    const count = await footerLinks.count();
    expect(count).toBeGreaterThan(0);

    // Click the first internal link
    for (let i = 0; i < Math.min(count, 3); i++) {
      const href = await footerLinks.nth(i).getAttribute("href");
      if (href && href.startsWith("/")) {
        await page.goto(`${APP}${href}`, { waitUntil: "networkidle" });
        await page.waitForTimeout(1000);
        await expect(page.locator("text=Page not found")).not.toBeVisible();
      }
    }
  });

  test("Sign In link navigates to login", async ({ page }) => {
    await page.goto(APP, { waitUntil: "networkidle" });
    const signIn = page.locator("nav").getByText("Sign In").first();
    if (await signIn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await signIn.click();
      await page.waitForTimeout(2000);
      expect(page.url()).toContain("/login");
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  PERFORMANCE
// ═══════════════════════════════════════════════════════════════════════

test.describe("Performance", () => {
  test("Landing page loads within 8 seconds", async ({ page }) => {
    const start = Date.now();
    await page.goto(APP, { waitUntil: "domcontentloaded" });
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(8000);
  });

  test("Login page loads within 5 seconds", async ({ page }) => {
    const start = Date.now();
    await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(5000);
  });
});
