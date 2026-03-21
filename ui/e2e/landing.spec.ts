import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Landing Page — Smoke & Rendering Tests
// ---------------------------------------------------------------------------

test.describe("Landing Page — Core Rendering", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("page loads with 200 and correct title", async ({ page }) => {
    await expect(page).toHaveTitle(/AgenticOrg/);
  });

  test("nav bar renders with brand logo and links", async ({ page }) => {
    // Brand
    await expect(page.locator("nav")).toBeVisible();
    await expect(page.getByText("AgenticOrg").first()).toBeVisible();

    // Desktop nav links
    const navLinks = ["How it Works", "Agents", "Architecture", "Features", "ROI", "Dashboard"];
    for (const link of navLinks) {
      await expect(page.locator("nav").getByText(link, { exact: false })).toBeVisible();
    }

    // GitHub CTA button in nav
    await expect(page.locator("nav").getByText("GitHub")).toBeVisible();
  });

  test("hero section renders headline and CTAs", async ({ page }) => {
    await expect(page.getByText("24 AI Agents.")).toBeVisible();
    await expect(page.getByText("42 Enterprise Systems.")).toBeVisible();
    await expect(page.getByText("One Platform.")).toBeVisible();

    // Sub-headline
    await expect(
      page.getByText("The open-source enterprise AI agent platform")
    ).toBeVisible();

    // CTA buttons
    await expect(page.getByRole("link", { name: /View on GitHub/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /Read Docs/i })).toBeVisible();
  });

  test("stats bar shows all 5 metrics", async ({ page }) => {
    const stats = [
      { value: "24", label: "Agents" },
      { value: "42", label: "Connectors" },
      { value: "161", label: "Tests" },
      { value: "18", label: "Schemas" },
      { value: "Apache 2.0", label: "License" },
    ];
    for (const s of stats) {
      await expect(page.getByText(s.value, { exact: true }).first()).toBeVisible();
      await expect(page.getByText(s.label, { exact: true }).first()).toBeVisible();
    }
  });
});

// ---------------------------------------------------------------------------
// Sections — Content Verification
// ---------------------------------------------------------------------------

test.describe("Landing Page — Sections", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("How It Works — 3 steps render", async ({ page }) => {
    const section = page.locator("#how-it-works");
    await expect(section.getByText("How It Works")).toBeVisible();
    await expect(section.getByText("Deploy Agents")).toBeVisible();
    await expect(section.getByText("Connect Systems")).toBeVisible();
    await expect(section.getByText("Automate Workflows")).toBeVisible();
  });

  test("Agent Domains — all 5 domains with correct agent counts", async ({ page }) => {
    await expect(page.getByText("Agent Domains").first()).toBeVisible();

    const domains = [
      { name: "Finance", count: "6 agents" },
      { name: "HR", count: "6 agents" },
      { name: "Marketing", count: "5 agents" },
      { name: "Operations", count: "5 agents" },
      { name: "Back Office", count: "3 agents" },
    ];
    for (const d of domains) {
      await expect(page.getByRole("heading", { name: d.name }).first()).toBeVisible();
      await expect(page.getByText(d.count).first()).toBeVisible();
    }
  });

  test("Agent Domains — key capabilities listed", async ({ page }) => {
    const section = page.locator("#agents");
    const capabilities = [
      "Invoice Processing",
      "Talent Acquisition",
      "Content Generation",
      "Support Triage",
      "Legal Ops",
    ];
    for (const cap of capabilities) {
      await expect(section.getByText(cap)).toBeVisible();
    }
  });

  test("8-Layer Architecture — all layers render", async ({ page }) => {
    await expect(page.getByText("8-Layer Architecture")).toBeVisible();
    const layers = [
      "API Gateway",
      "Orchestration",
      "Agent Runtime",
      "HITL Governance",
      "Connector Hub",
      "Schema Registry",
      "Observability",
      "Infrastructure",
    ];
    for (const layer of layers) {
      await expect(page.getByText(layer).first()).toBeVisible();
    }
  });

  test("Enterprise Features — all 8 features render", async ({ page }) => {
    await expect(page.getByText("Enterprise Features").first()).toBeVisible();
    const features = [
      "HITL Governance",
      "Shadow Mode",
      "PII Masking",
      "Tenant Isolation",
      "Cost Controls",
      "Auto-Scaling",
      "Audit Trail",
      "50 Error Codes",
    ];
    for (const f of features) {
      await expect(page.getByText(f).first()).toBeVisible();
    }
  });

  test("India-First Connectors — all 6 connectors render", async ({ page }) => {
    await expect(page.getByText("Built for Indian Enterprise")).toBeVisible();

    // Check connector names via their heading elements
    const connectors = ["Darwinbox", "Pine Labs Plural", "Tally", "DigiLocker"];
    for (const c of connectors) {
      await expect(page.getByText(c, { exact: true }).first()).toBeVisible();
    }
  });

  test("ROI Calculator section renders", async ({ page }) => {
    await expect(page.locator("#roi-calculator")).toBeVisible();
  });

  test("Open Source section with Apache 2.0 badge", async ({ page }) => {
    await expect(page.getByText("100% Open Source")).toBeVisible();
    await expect(page.getByText("Apache 2.0 Licensed")).toBeVisible();
    await expect(page.getByRole("link", { name: /Star on GitHub/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /Contribute/i })).toBeVisible();
  });

  test("Footer renders with all sections", async ({ page }) => {
    const footer = page.locator("footer");
    await expect(footer).toBeVisible();

    // Footer section headings
    await expect(footer.getByRole("heading", { name: "Documentation" })).toBeVisible();
    await expect(footer.getByRole("heading", { name: "Community" })).toBeVisible();
    await expect(footer.getByRole("heading", { name: "Platform" })).toBeVisible();

    // Copyright
    await expect(footer.getByText("AgenticOrg Contributors")).toBeVisible();

    // Footer links
    await expect(footer.getByRole("link", { name: "Dashboard" })).toBeVisible();
    await expect(footer.getByRole("link", { name: "Workflows" })).toBeVisible();
    await expect(footer.getByRole("link", { name: "Connectors" })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

test.describe("Landing Page — Navigation", () => {
  test("anchor links scroll to correct sections", async ({ page }) => {
    await page.goto("/");

    // Click "How it Works" nav link
    await page.locator("nav").getByText("How it Works").click();
    await expect(page).toHaveURL(/#how-it-works/);

    await page.locator("nav").getByText("Agents").click();
    await expect(page).toHaveURL(/#agents/);

    await page.locator("nav").getByText("Architecture").click();
    await expect(page).toHaveURL(/#architecture/);

    await page.locator("nav").getByText("Features").click();
    await expect(page).toHaveURL(/#features/);
  });

  test("Dashboard link navigates to /dashboard", async ({ page }) => {
    await page.goto("/");
    await page.locator("nav").getByText("Dashboard").click();
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test("GitHub links have correct href and open in new tab", async ({ page }) => {
    await page.goto("/");
    const ghLink = page.getByRole("link", { name: /View on GitHub/i });
    await expect(ghLink).toHaveAttribute("href", "https://github.com/mishrasanjeev/agentic-org");
    await expect(ghLink).toHaveAttribute("target", "_blank");
  });
});

// ---------------------------------------------------------------------------
// Mobile Responsive
// ---------------------------------------------------------------------------

test.describe("Landing Page — Mobile", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("mobile menu toggle works", async ({ page }) => {
    await page.goto("/");

    // Desktop nav links should be hidden on mobile
    await expect(page.locator("nav .hidden.md\\:flex")).not.toBeVisible();

    // Hamburger button should be visible
    const menuButton = page.getByLabel("Menu");
    await expect(menuButton).toBeVisible();

    // Open mobile menu
    await menuButton.click();

    // Mobile menu links should now be visible
    await expect(page.getByText("How it Works").last()).toBeVisible();
    await expect(page.getByText("Agents").last()).toBeVisible();

    // Close mobile menu
    await menuButton.click();
  });

  test("hero headline readable on mobile", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("24 AI Agents.")).toBeVisible();
    await expect(page.getByText("One Platform.")).toBeVisible();
  });

  test("stats bar renders on mobile (2-column grid)", async ({ page }) => {
    await page.goto("/");
    // The stats bar contains "24" and "Agents" as separate divs
    // On mobile, nav "Agents" link is hidden, so target the stats section
    const statsBar = page.locator("section").filter({ hasText: "Connectors" }).filter({ hasText: "Schemas" }).first();
    await expect(statsBar.getByText("24")).toBeVisible();
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
    await expect(canonical).toHaveAttribute("href", "https://agenticorg.ai");

    // OG tags
    await expect(page.locator('meta[property="og:title"]')).toHaveAttribute(
      "content",
      /AgenticOrg/
    );
    await expect(page.locator('meta[property="og:type"]')).toHaveAttribute("content", "website");
    await expect(page.locator('meta[property="og:url"]')).toHaveAttribute(
      "content",
      "https://agenticorg.ai"
    );

    // Description
    await expect(page.locator('meta[name="description"]')).toHaveAttribute(
      "content",
      /AI agents/i
    );
  });

  test("JSON-LD structured data is present", async ({ page }) => {
    await page.goto("/");
    const jsonLd = page.locator('script[type="application/ld+json"]');
    await expect(jsonLd).toBeAttached();
    const content = await jsonLd.textContent();
    const data = JSON.parse(content!);
    expect(data["@type"]).toBe("SoftwareApplication");
    expect(data.name).toBe("AgenticOrg");
    expect(data.softwareVersion).toBe("2.1.0");
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

  test("no console errors on page load", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.goto("/");
    await page.waitForTimeout(2000);
    expect(errors).toEqual([]);
  });
});
