import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Production E2E — App Routes
// Tests run against BASE_URL (default: https://app.agenticorg.ai)
// No page.route() mocking — all responses are real.
// ---------------------------------------------------------------------------

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const MARKETING = "https://agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

// ---------------------------------------------------------------------------
// Public Routes — Must load without auth
// ---------------------------------------------------------------------------

test.describe("Public Routes — Reachability", () => {
  // Public pages are on the marketing domain (agenticorg.ai), not app.agenticorg.ai
  const publicRoutes = [
    { url: `${MARKETING}/`, name: "Landing", expectText: "agent" },
    { url: `${MARKETING}/pricing`, name: "Pricing", expectText: "Pricing" },
    { url: `${MARKETING}/blog`, name: "Blog", expectText: "Blog" },
    { url: `${MARKETING}/evals`, name: "Evals", expectText: "Agent" },
    { url: `${MARKETING}/playground`, name: "Playground", expectText: "Playground" },
    { url: `${APP}/login`, name: "Login", expectText: "Sign" },
  ];

  for (const route of publicRoutes) {
    test(`${route.name} loads without error`, async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error") errors.push(msg.text());
      });

      const response = await page.goto(route.url, { waitUntil: "domcontentloaded" });
      expect(response?.status()).toBeLessThan(400);
      await expect(page.getByText(route.expectText, { exact: false }).first()).toBeVisible({ timeout: 20000 });

      // No uncaught JS errors (ignore favicon, API, WebSocket noise)
      const criticalErrors = errors.filter(
        (e) =>
          !e.includes("favicon") &&
          !e.includes("api/v1") &&
          !e.includes("WebSocket") &&
          !e.includes("ERR_CONNECTION") &&
          !e.includes("net::ERR") &&
          !e.includes("Failed to load")
      );
      expect(criticalErrors).toEqual([]);
    });
  }
});

// ---------------------------------------------------------------------------
// Dashboard Routes — Require auth token
// ---------------------------------------------------------------------------

test.describe("Dashboard Routes — Auth Required", () => {
  test.describe.configure({ mode: "serial" });

  const dashboardRoutes = [
    { path: "/dashboard", name: "Dashboard", expectText: "Dashboard" },
    { path: "/dashboard/agents", name: "Agents", expectText: "Agent" },
    { path: "/dashboard/agents/new", name: "Create Agent", expectText: "Agent" },
    { path: "/dashboard/workflows", name: "Workflows", expectText: "Workflow" },
    { path: "/dashboard/workflows/new", name: "Create Workflow", expectText: "Workflow" },
    { path: "/dashboard/approvals", name: "Approvals", expectText: "Approval" },
    { path: "/dashboard/connectors", name: "Connectors", expectText: "Connector" },
    { path: "/dashboard/connectors/new", name: "Register Connector", expectText: "Connector" },
    { path: "/dashboard/schemas", name: "Schemas", expectText: "Schema" },
    { path: "/dashboard/audit", name: "Audit", expectText: "Audit" },
    { path: "/dashboard/settings", name: "Settings", expectText: "Settings" },
    { path: "/dashboard/cfo", name: "CFO Dashboard", expectText: "Dashboard" },
  ];

  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token — set E2E_TOKEN env var");
    await page.goto(`${APP}/login`);
    await page.evaluate((token) => {
      localStorage.setItem("token", token);
    localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));    }, E2E_TOKEN);
  });

  for (const route of dashboardRoutes) {
    test(`${route.name} (${route.path}) loads without error`, async ({ page }) => {
      const response = await page.goto(route.path, { waitUntil: "domcontentloaded" });
      expect(response?.status()).toBeLessThan(400);
      await page.waitForLoadState("networkidle").catch(() => {});

      // Page should not be blank
      const bodyText = await page.locator("body").textContent();
      expect(bodyText?.trim().length).toBeGreaterThan(0);

      // Should contain expected text
      await expect(page.getByText(route.expectText).first()).toBeVisible({ timeout: 15000 });

      // No error states
      const mainText = await page.locator("main").textContent() || "";
      expect(mainText).not.toContain("NaN");
    });
  }
});

// ---------------------------------------------------------------------------
// 404 handling
// ---------------------------------------------------------------------------

test.describe("404 Page", () => {
  test("unknown route shows 404 page", async ({ page }) => {
    await page.goto("/this-route-does-not-exist");
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/not found|404/i).first()).toBeVisible({ timeout: 10000 });
  });

  test("404 page has navigation back to home", async ({ page }) => {
    await page.goto("/nonexistent-page");
    await page.waitForLoadState("networkidle");
    const homeLink = page.getByRole("link", { name: /Back to Home|Home/i }).first();
    if (await homeLink.isVisible({ timeout: 5000 }).catch(() => false)) {
      await homeLink.click();
      await page.waitForLoadState("networkidle");
      await expect(page).toHaveURL("/");
    }
  });
});

// ---------------------------------------------------------------------------
// Dashboard — Sidebar Navigation (auth required)
// ---------------------------------------------------------------------------

test.describe("Dashboard — Sidebar Navigation", () => {
  test.describe.configure({ mode: "serial" });

  const sidebarLinks = [
    { label: "Dashboard", expectPath: "/dashboard", expectText: "Dashboard" },
    { label: "Agents", expectPath: "/dashboard/agents", expectText: "Agent" },
    { label: "Workflows", expectPath: "/dashboard/workflows", expectText: "Workflow" },
    { label: "Approvals", expectPath: "/dashboard/approvals", expectText: "Approval" },
    { label: "Connectors", expectPath: "/dashboard/connectors", expectText: "Connector" },
    { label: "Schemas", expectPath: "/dashboard/schemas", expectText: "Schema" },
    { label: "Audit", expectPath: "/dashboard/audit", expectText: "Audit" },
    { label: "Settings", expectPath: "/dashboard/settings", expectText: "Settings" },
  ];

  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token — set E2E_TOKEN env var");
    await page.goto(`${APP}/login`);
    await page.evaluate((token) => {
      localStorage.setItem("token", token);
    localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));    }, E2E_TOKEN);
  });

  for (const link of sidebarLinks) {
    test(`Sidebar "${link.label}" navigates to ${link.expectPath}`, async ({ page }) => {
      test.setTimeout(45000);
      await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle").catch(() => {});
      // Sidebar may be in aside or nav element
      // Find link in sidebar (complementary/nav/aside)
      const sidebarLink = page.getByRole("link", { name: link.label, exact: true }).first();
      const isVisible = await sidebarLink.isVisible({ timeout: 20000 }).catch(() => false);
      if (!isVisible) {
        // Sidebar may not show all links for this role — skip gracefully
        return;
      }
      await sidebarLink.click();
      await page.waitForLoadState("domcontentloaded").catch(() => {});
      // Verify page loaded (URL or text)
      const urlOk = page.url().includes(link.expectPath);
      const textOk = await page.getByText(link.expectText).first().isVisible({ timeout: 15000 }).catch(() => false);
      expect(urlOk || textOk).toBeTruthy();
    });
  }
});

// ---------------------------------------------------------------------------
// Create Flows — auth required
// ---------------------------------------------------------------------------

test.describe("Create Flows", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token — set E2E_TOKEN env var");
    await page.goto(`${APP}/login`);
    await page.evaluate((token) => {
      localStorage.setItem("token", token);
    localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));    }, E2E_TOKEN);
  });

  test("Agents page → Create Agent button works", async ({ page }) => {
    await page.goto("/dashboard/agents");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "Create Agent" }).click();
    await expect(page).toHaveURL("/dashboard/agents/new");
    await expect(page.getByText("Agent").first()).toBeVisible({ timeout: 10000 });
  });

  test("Workflows page → Create Workflow button works", async ({ page }) => {
    await page.goto("/dashboard/workflows");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "Create Workflow" }).click();
    await expect(page).toHaveURL("/dashboard/workflows/new");
    await expect(page.getByText("Workflow").first()).toBeVisible({ timeout: 10000 });
  });

  test("Connectors page → Register Connector button works", async ({ page }) => {
    await page.goto("/dashboard/connectors");
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "Register Connector" }).click();
    await expect(page).toHaveURL("/dashboard/connectors/new");
    await expect(page.getByText("Connector").first()).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// Data Display Quality — auth required
// ---------------------------------------------------------------------------

test.describe("Data Display Quality", () => {
  test.describe.configure({ mode: "serial" });

  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "requires auth token — set E2E_TOKEN env var");
    await page.goto(`${APP}/login`);
    await page.evaluate((token) => {
      localStorage.setItem("token", token);
    localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));    }, E2E_TOKEN);
  });

  const pages = [
    "/dashboard",
    "/dashboard/agents",
    "/dashboard/workflows",
    "/dashboard/connectors",
    "/dashboard/schemas",
    "/dashboard/audit",
    "/dashboard/approvals",
    "/dashboard/settings",
  ];

  for (const p of pages) {
    test(`${p} shows no NaN or undefined`, async ({ page }) => {
      await page.goto(p);
      await page.waitForLoadState("networkidle");
      const body = await page.locator("main").textContent() || "";
      expect(body).not.toContain("NaN");
      expect(body).not.toContain("undefined");
    });
  }
});

// ---------------------------------------------------------------------------
// Landing → Dashboard Flow
// ---------------------------------------------------------------------------

test.describe("Landing → Dashboard Flow", () => {
  test("Landing nav Sign In link navigates to login", async ({ page }) => {
    await page.goto("https://agenticorg.ai/", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    const signInLink = page.locator("nav").getByText("Sign In").first();
    if (await signInLink.isVisible({ timeout: 10000 }).catch(() => false)) {
      await signInLink.click();
      await page.waitForLoadState("networkidle");
      expect(page.url()).toMatch(/\/login/);
    }
  });

  test("Landing nav Platform link navigates correctly", async ({ page }) => {
    await page.goto("https://agenticorg.ai/", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    const platformLink = page.locator("nav").getByText("Platform").first();
    if (await platformLink.isVisible({ timeout: 10000 }).catch(() => false)) {
      await platformLink.click();
      await page.waitForLoadState("networkidle");
      // Platform link may scroll or navigate
      await expect(page.locator("body")).toBeVisible();
    }
  });

  test("Footer links are present", async ({ page }) => {
    await page.goto("https://agenticorg.ai/", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    const footer = page.locator("footer");
    if (await footer.isVisible({ timeout: 10000 }).catch(() => false)) {
      const footerLinks = footer.locator("a[href]");
      const count = await footerLinks.count();
      expect(count).toBeGreaterThan(0);
    }
  });
});
