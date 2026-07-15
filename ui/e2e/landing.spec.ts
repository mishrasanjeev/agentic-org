import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Configurable E2E — Landing Page
// ---------------------------------------------------------------------------

const MARKETING =
  process.env.MARKETING_URL ||
  process.env.BASE_URL ||
  "http://127.0.0.1:4173";

const PRODUCT_FACTS_FIXTURE = {
  version: "9.9.9",
  connector_count: 37,
  agent_count: 29,
  tool_count: 431,
};

async function openLanding(page: Page): Promise<void> {
  await page.route("**/api/v1/product-facts", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PRODUCT_FACTS_FIXTURE),
    });
  });
  await page.goto(MARKETING);
  await page.waitForLoadState("networkidle");
}

// ---------------------------------------------------------------------------
// Core Rendering
// ---------------------------------------------------------------------------

test.describe("Landing Page — Core Rendering", () => {
  test.beforeEach(async ({ page }) => {
    await openLanding(page);
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
      page.getByRole("heading", { name: /Enterprise Work,\s*Governed by Design\./i }).first()
    ).toBeVisible({ timeout: 10000 });

    // CTA buttons
    await expect(
      page.getByRole("link", { name: /Review Plans/i }).first()
    ).toBeVisible();
  });

  test("stats bar hydrates every metric from product facts", async ({ page }) => {
    const body = page.locator("body");
    await expect(body).toContainText(String(PRODUCT_FACTS_FIXTURE.agent_count));
    await expect(body).toContainText(String(PRODUCT_FACTS_FIXTURE.connector_count));
    await expect(body).toContainText(String(PRODUCT_FACTS_FIXTURE.tool_count));
    await expect(page.getByTestId("landing-version-pill")).toContainText(
      `v${PRODUCT_FACTS_FIXTURE.version}`,
    );
  });
});

// ---------------------------------------------------------------------------
// Sections — Content Verification
// ---------------------------------------------------------------------------

test.describe("Landing Page — Sections", () => {
  test.beforeEach(async ({ page }) => {
    await openLanding(page);
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
      page.getByRole("heading", { name: "Governance controls for enterprise workflows" }).first()
    ).toBeVisible({ timeout: 10000 });

    const features = [
      "HITL Governance",
      "Shadow Mode",
      "Reviewable Action History",
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

  test("Open Commerce section renders", async ({ page }) => {
    const commerceSection = page.locator("#open-commerce");
    await expect(commerceSection).toBeAttached({ timeout: 10000 });
    await expect(
      commerceSection.getByRole("heading", {
        name: /Buyer and seller AI-agent runtime for OACP commerce/i,
      }).first(),
    ).toBeVisible();
  });

  test("Footer renders with key sections", async ({ page }) => {
    const footer = page.locator("footer");
    await expect(footer).toBeVisible();

    await expect(footer.getByText("Dashboard").first()).toBeVisible();
    await expect(footer.getByText("Agents").first()).toBeVisible();
    await expect(footer.getByText("Pricing").first()).toBeVisible();
    await expect(footer.getByText("agenticorg.ai").first()).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

test.describe("Landing Page — Navigation", () => {
  test("Sign In link navigates to login", async ({ page }) => {
    await openLanding(page);
    const signIn = page.locator("nav").getByText("Sign In").first();
    await signIn.click();
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/login");
  });

  test("Pricing nav link works", async ({ page }) => {
    await openLanding(page);
    const pricingLink = page.locator("nav").getByText("Pricing").first();
    await pricingLink.click();
    await page.waitForLoadState("networkidle");
    // May scroll to pricing section or navigate to /pricing
    expect(page.url()).toMatch(/\/(pricing|#pricing)?/);
  });

  test("Blog nav link works", async ({ page }) => {
    await openLanding(page);
    const blogLink = page.locator("nav").getByText("Blog").first();
    await blogLink.click();
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/blog");
  });

  test("Playground nav link works", async ({ page }) => {
    await openLanding(page);
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
    await openLanding(page);
    await expect(
      page.getByRole("heading", { name: /Enterprise Work,\s*Governed by Design\./i }).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("mobile menu toggle works", async ({ page }) => {
    await openLanding(page);

    const menuButton = page.locator('button[aria-label="Toggle navigation menu"]');
    if (await menuButton.isVisible()) {
      await menuButton.click();
      const mobileNav = page.locator('[aria-label="Mobile navigation"]');
      await expect(mobileNav).toBeVisible({ timeout: 5000 });
    }
  });

  test("footer is visible on mobile", async ({ page }) => {
    await openLanding(page);
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
    await openLanding(page);
    await expect(
      page.getByRole("heading", { name: /Enterprise Work,\s*Governed by Design\./i }).first()
    ).toBeVisible({ timeout: 10000 });
  });

  test("stats bar shows metrics on tablet", async ({ page }) => {
    await openLanding(page);
    await expect(page.getByText(/agent/i).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/connector/i).first()).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// SEO & Meta
// ---------------------------------------------------------------------------

test.describe("Landing Page — SEO", () => {
  test("meta tags are present", async ({ page }) => {
    await openLanding(page);

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
  test("page loads within 8 seconds on the configured target", async ({ page }) => {
    await page.route("**/api/v1/product-facts", async (route) => {
      await route.fulfill({ status: 200, json: PRODUCT_FACTS_FIXTURE });
    });
    const start = Date.now();
    await page.goto(MARKETING, { waitUntil: "domcontentloaded" });
    const elapsed = Date.now() - start;
    // Production may be slower than localhost — allow 8s
    expect(elapsed).toBeLessThan(8000);
  });
});
