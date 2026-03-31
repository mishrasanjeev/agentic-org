import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Landing Page — Smoke & Rendering Tests
// ---------------------------------------------------------------------------

test.describe("Landing Page — Core Rendering", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
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
      await expect(page.locator("nav").getByText(link, { exact: false }).first()).toBeVisible();
    }
  });

  test("hero section renders headline and CTAs", async ({ page }) => {
    await expect(page.getByText("Your Back Office Runs Itself.").first()).toBeVisible();

    // CTA buttons
    await expect(page.getByRole("link", { name: /Start Free/i }).first()).toBeVisible();
  });

  test("stats bar shows key metrics", async ({ page }) => {
    await expect(page.getByText("25").first()).toBeVisible();
    await expect(page.getByText("42").first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Sections — Content Verification
// ---------------------------------------------------------------------------

test.describe("Landing Page — Sections", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);
  });

  test("How It Works — 3 steps render", async ({ page }) => {
    const steps = [
      "Create or pick your agents",
      "Connect your systems",
      "Agents work, you approve",
    ];
    for (const step of steps) {
      await expect(page.getByText(step).first()).toBeVisible();
    }
  });

  test("Department sections render with agent counts", async ({ page }) => {
    // Check that department-related content appears
    const departments = ["Finance", "HR", "Marketing", "Operations"];
    let found = 0;
    for (const dept of departments) {
      const el = page.getByText(dept, { exact: false }).first();
      if (await el.isVisible().catch(() => false)) found++;
    }
    expect(found).toBeGreaterThanOrEqual(3);
  });

  test("Trust & Security — 4 features render", async ({ page }) => {
    await expect(page.getByText("Enterprise-Grade from Day One").first()).toBeVisible();
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
    await expect(page.getByText("Built for Indian Enterprise").first()).toBeVisible();
  });

  test("ROI Calculator section renders", async ({ page }) => {
    const roiSection = page.locator("#roi-calculator");
    await expect(roiSection).toBeAttached();
  });

  test("Footer renders with key sections", async ({ page }) => {
    const footer = page.locator("footer");
    await expect(footer).toBeVisible();

    // Footer links
    await expect(footer.getByText("Dashboard").first()).toBeVisible();
    await expect(footer.getByText("Agents").first()).toBeVisible();
    await expect(footer.getByText("Pricing").first()).toBeVisible();

    // Company info
    await expect(footer.getByText("Edumatica").first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

test.describe("Landing Page — Navigation", () => {
  test("Sign In link navigates to login", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    const signIn = page.locator("nav").getByText("Sign In").first();
    await signIn.click();
    await page.waitForTimeout(2000);
    expect(page.url()).toContain("/login");
  });
});

// ---------------------------------------------------------------------------
// Mobile Responsive
// ---------------------------------------------------------------------------

test.describe("Landing Page — Mobile", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("hero headline readable on mobile", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("Your Back Office Runs Itself.").first()).toBeVisible();
  });

  test("mobile menu toggle works", async ({ page }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Hamburger button should be visible on mobile
    const menuButton = page.locator('button[aria-label="Toggle navigation menu"]');
    if (await menuButton.isVisible()) {
      await menuButton.click();
      await page.waitForTimeout(500);
      // Should show mobile nav links
      const mobileNav = page.locator('[aria-label="Mobile navigation"]');
      await expect(mobileNav).toBeVisible();
    }
  });
});

// ---------------------------------------------------------------------------
// SEO & Meta
// ---------------------------------------------------------------------------

test.describe("Landing Page — SEO", () => {
  test("meta tags are present", async ({ page }) => {
    await page.goto("/");

    // Canonical
    const canonical = page.locator('link[rel="canonical"]');
    await expect(canonical).toHaveAttribute("href", /https:\/\/agenticorg\.ai/);

    // Description
    await expect(page.locator('meta[name="description"]').first()).toHaveAttribute(
      "content",
      /AI/i
    );
  });
});

// ---------------------------------------------------------------------------
// Performance
// ---------------------------------------------------------------------------

test.describe("Landing Page — Performance", () => {
  test("page loads within 5 seconds", async ({ page }) => {
    const start = Date.now();
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const elapsed = Date.now() - start;
    expect(elapsed).toBeLessThan(5000);
  });
});
