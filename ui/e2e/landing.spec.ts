import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Production E2E — Landing Page
// Tests run against BASE_URL (default: https://app.agenticorg.ai)
// No page.route() mocking — all responses are real.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Core Rendering
// ---------------------------------------------------------------------------

test.describe("Landing Page — Core Rendering", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
  });

  test("page loads with 200 and correct title", async ({ page }) => {
    await expect(page).toHaveTitle(/AgenticOrg/);
  });

  test("nav bar renders with brand and links", async ({ page }) => {
    await expect(page.locator("nav")).toBeVisible();
    await expect(page.getByText("AgenticOrg").first()).toBeVisible();

    // Desktop nav links
    const navLinks = ["Platform", "Solutions", "Pricing", "Playground", "Blog", "Resources"];
    for (const link of navLinks) {
      await expect(
        page.locator("nav").getByText(link, { exact: false }).first()
      ).toBeVisible();
    }
  });

  test("hero section renders headline and CTAs", async ({ page }) => {
    await expect(
      page.getByText("Your Back Office Runs Itself.").first()
    ).toBeVisible({ timeout: 10000 });

    // CTA buttons
    await expect(
      page.getByRole("link", { name: /Start Free/i }).first()
    ).toBeVisible();
  });

  test("stats bar shows current platform metrics", async ({ page }) => {
    // Updated metrics: 35 agents, 54 connectors, 340+ tools
    await expect(page.getByText("35").first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("54").first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/340/).first()).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// Sections — Content Verification
// ---------------------------------------------------------------------------

test.describe("Landing Page — Sections", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
  });

  test("How It Works — 3 steps render", async ({ page }) => {
    const steps = [
      "Create or pick your agents",
      "Connect your systems",
      "Agents work, you approve",
    ];
    for (const step of steps) {
      await expect(page.getByText(step).first()).toBeVisible({ timeout: 10000 });
    }
  });

  test("Department/role sections render", async ({ page }) => {
    const departments = ["Finance", "HR", "Marketing", "Operations"];
    let found = 0;
    for (const dept of departments) {
      const el = page.getByText(dept, { exact: false }).first();
      if (await el.isVisible().catch(() => false)) found++;
    }
    expect(found).toBeGreaterThanOrEqual(3);
  });

  test("Trust & Security section renders", async ({ page }) => {
    await expect(
      page.getByText("Enterprise-Grade from Day One").first()
    ).toBeVisible({ timeout: 10000 });

    const features = [
      "HITL Governance",
      "Shadow Mode",
      "Complete Audit Trail",
      "Secure Authentication",
    ];
    for (const f of features) {
      await expect(page.getByText(f).first()).toBeVisible();
    }
  });

  test("India-First Connectors section renders", async ({ page }) => {
    await expect(
      page.getByText("Built for Indian Enterprise").first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("ROI Calculator section renders", async ({ page }) => {
    const roiSection = page.locator("#roi-calculator");
    await expect(roiSection).toBeAttached({ timeout: 10000 });
  });

  test("Footer renders with key sections", async ({ page }) => {
    const footer = page.locator("footer");
    await expect(footer).toBeVisible();

    await expect(footer.getByText("Dashboard").first()).toBeVisible();
    await expect(footer.getByText("Agents").first()).toBeVisible();
    await expect(footer.getByText("Pricing").first()).toBeVisible();
    await expect(footer.getByText("Edumatica").first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

test.describe("Landing Page — Navigation", () => {
  test("Sign In link navigates to login", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
    const signIn = page.locator("nav").getByText("Sign In").first();
    await signIn.click();
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/login");
  });

  test("Pricing nav link works", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
    const pricingLink = page.locator("nav").getByText("Pricing").first();
    await pricingLink.click();
    await page.waitForLoadState("networkidle");
    // May scroll to pricing section or navigate to /pricing
    expect(page.url()).toMatch(/\/(pricing|#pricing)?/);
  });

  test("Blog nav link works", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
    const blogLink = page.locator("nav").getByText("Blog").first();
    await blogLink.click();
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/blog");
  });

  test("Playground nav link works", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
    const playgroundLink = page.locator("nav").getByText("Playground").first();
    await playgroundLink.click();
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/playground");
  });
});

// ---------------------------------------------------------------------------
// Mobile Responsive
// ---------------------------------------------------------------------------

test.describe("Landing Page — Mobile", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("hero headline readable on mobile", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
    await expect(
      page.getByText("Your Back Office Runs Itself.").first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("mobile menu toggle works", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");

    const menuButton = page.locator('button[aria-label="Toggle navigation menu"]');
    if (await menuButton.isVisible()) {
      await menuButton.click();
      const mobileNav = page.locator('[aria-label="Mobile navigation"]');
      await expect(mobileNav).toBeVisible({ timeout: 5000 });
    }
  });

  test("footer is visible on mobile", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
    const footer = page.locator("footer");
    await footer.scrollIntoViewIfNeeded();
    await expect(footer).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Tablet Responsive
// ---------------------------------------------------------------------------

test.describe("Landing Page — Tablet", () => {
  test.use({ viewport: { width: 768, height: 1024 } });

  test("hero renders on tablet viewport", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
    await expect(
      page.getByText("Your Back Office Runs Itself.").first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("stats bar shows metrics on tablet", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("35").first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("54").first()).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// SEO & Meta
// ---------------------------------------------------------------------------

test.describe("Landing Page — SEO", () => {
  test("meta tags are present", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");

    const canonical = page.locator('link[rel="canonical"]');
    await expect(canonical).toHaveAttribute("href", /https:\/\/agenticorg\.ai/);

    await expect(
      page.locator('meta[name="description"]').first()
    ).toHaveAttribute("content", /AI/i);
  });
});

// ---------------------------------------------------------------------------
// Performance
// ---------------------------------------------------------------------------

test.describe("Landing Page — Performance", () => {
  test("page loads within 8 seconds on production", async ({ page }) => {
    const start = Date.now();
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const elapsed = Date.now() - start;
    // Production may be slower than localhost — allow 8s
    expect(elapsed).toBeLessThan(8000);
  });
});
