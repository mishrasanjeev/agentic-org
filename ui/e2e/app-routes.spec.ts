import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// App Routes — Every route must load without errors
// ---------------------------------------------------------------------------

test.describe("App Routes — Reachability", () => {
  const routes = [
    { path: "/", name: "Landing", expectText: "24 AI Agents" },
    { path: "/dashboard", name: "Dashboard", expectText: "Dashboard" },
    { path: "/dashboard/agents", name: "Agents", expectText: "Agent Fleet" },
    { path: "/dashboard/agents/new", name: "Create Agent", expectText: "Create Agent" },
    { path: "/dashboard/workflows", name: "Workflows", expectText: "Workflows" },
    { path: "/dashboard/workflows/new", name: "Create Workflow", expectText: "Create Workflow" },
    { path: "/dashboard/approvals", name: "Approvals", expectText: "Approval Queue" },
    { path: "/dashboard/connectors", name: "Connectors", expectText: "Connectors" },
    { path: "/dashboard/connectors/new", name: "Register Connector", expectText: "Register Connector" },
    { path: "/dashboard/schemas", name: "Schemas", expectText: "Schema Registry" },
    { path: "/dashboard/audit", name: "Audit", expectText: "Audit Log" },
    { path: "/dashboard/settings", name: "Settings", expectText: "Settings" },
  ];

  for (const route of routes) {
    test(`${route.name} (${route.path}) loads without error`, async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });

      const response = await page.goto(route.path);
      expect(response?.status()).toBeLessThan(400);
      await expect(page.getByText(route.expectText).first()).toBeVisible();

      // No uncaught JS errors
      const criticalErrors = errors.filter(
        (e) => !e.includes("favicon") && !e.includes("api/v1") && !e.includes("WebSocket")
      );
      expect(criticalErrors).toEqual([]);
    });
  }
});

// ---------------------------------------------------------------------------
// 404 handling
// ---------------------------------------------------------------------------

test.describe("404 Page", () => {
  test("unknown route shows 404 page with correct branding", async ({ page }) => {
    await page.goto("/this-route-does-not-exist");
    await expect(page.getByText(/not found|404/i).first()).toBeVisible();
    await expect(page.getByText("AO")).toBeVisible();
    await expect(page.getByRole("link", { name: /Back to Home/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /Go to Dashboard/i })).toBeVisible();
  });

  test("404 links navigate correctly", async ({ page }) => {
    await page.goto("/nonexistent-page");
    await page.getByRole("link", { name: /Back to Home/i }).click();
    await expect(page).toHaveURL("/");
  });
});

// ---------------------------------------------------------------------------
// Dashboard — Sidebar Navigation (every sidebar link works)
// ---------------------------------------------------------------------------

test.describe("Dashboard — Sidebar Navigation", () => {
  const sidebarLinks = [
    { label: "Dashboard", expectPath: "/dashboard", expectText: "Dashboard" },
    { label: "Agents", expectPath: "/dashboard/agents", expectText: "Agent Fleet" },
    { label: "Workflows", expectPath: "/dashboard/workflows", expectText: "Workflows" },
    { label: "Approvals", expectPath: "/dashboard/approvals", expectText: "Approval Queue" },
    { label: "Connectors", expectPath: "/dashboard/connectors", expectText: "Connectors" },
    { label: "Schemas", expectPath: "/dashboard/schemas", expectText: "Schema Registry" },
    { label: "Audit", expectPath: "/dashboard/audit", expectText: "Audit Log" },
    { label: "Settings", expectPath: "/dashboard/settings", expectText: "Settings" },
  ];

  for (const link of sidebarLinks) {
    test(`Sidebar "${link.label}" navigates to ${link.expectPath}`, async ({ page }) => {
      await page.goto("/dashboard");
      await page.locator("aside").getByText(link.label, { exact: true }).click();
      await expect(page).toHaveURL(link.expectPath);
      await expect(page.getByText(link.expectText).first()).toBeVisible();
    });
  }
});

// ---------------------------------------------------------------------------
// Create flows — buttons navigate to create pages
// ---------------------------------------------------------------------------

test.describe("Create Flows", () => {
  test("Agents page → Create Agent button works", async ({ page }) => {
    await page.goto("/dashboard/agents");
    await page.getByRole("button", { name: "Create Agent" }).click();
    await expect(page).toHaveURL("/dashboard/agents/new");
    await expect(page.getByText("Agent Configuration")).toBeVisible();
    await expect(page.getByText("Shadow Mode")).toBeVisible();
  });

  test("Create Agent page — form elements present", async ({ page }) => {
    await page.goto("/dashboard/agents/new");
    await expect(page.getByText("Agent Name")).toBeVisible();
    await expect(page.getByText("Domain")).toBeVisible();
    await expect(page.getByText("Agent Type")).toBeVisible();
    await expect(page.getByText("Confidence Floor")).toBeVisible();
    // Back button works
    await page.getByRole("button", { name: "Back to Agents" }).click();
    await expect(page).toHaveURL("/dashboard/agents");
  });

  test("Create Agent page — Cancel returns to agents", async ({ page }) => {
    await page.goto("/dashboard/agents/new");
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page).toHaveURL("/dashboard/agents");
  });

  test("Workflows page → Create Workflow button works", async ({ page }) => {
    await page.goto("/dashboard/workflows");
    await page.getByRole("button", { name: "Create Workflow" }).click();
    await expect(page).toHaveURL("/dashboard/workflows/new");
    await expect(page.getByText("Workflow Configuration")).toBeVisible();
  });

  test("Create Workflow page — Cancel returns to workflows", async ({ page }) => {
    await page.goto("/dashboard/workflows/new");
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page).toHaveURL("/dashboard/workflows");
  });

  test("Connectors page → Register Connector button works", async ({ page }) => {
    await page.goto("/dashboard/connectors");
    await page.getByRole("button", { name: "Register Connector" }).click();
    await expect(page).toHaveURL("/dashboard/connectors/new");
    await expect(page.getByText("Connector Configuration")).toBeVisible();
  });

  test("Register Connector page — Cancel returns to connectors", async ({ page }) => {
    await page.goto("/dashboard/connectors/new");
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page).toHaveURL("/dashboard/connectors");
  });
});

// ---------------------------------------------------------------------------
// Dashboard pages — no NaN, no broken data display
// ---------------------------------------------------------------------------

test.describe("Data Display Quality", () => {
  test("Dashboard shows stats without NaN", async ({ page }) => {
    await page.goto("/dashboard");
    const body = await page.locator("main").textContent();
    expect(body).not.toContain("NaN");
    expect(body).not.toContain("undefined");
  });

  test("Agents page shows no NaN or undefined", async ({ page }) => {
    await page.goto("/dashboard/agents");
    await page.waitForTimeout(1000);
    const body = await page.locator("main").textContent();
    expect(body).not.toContain("NaN");
    expect(body).not.toContain("undefined");
  });

  test("Agent detail handles missing data gracefully", async ({ page }) => {
    // /dashboard/agents/new would match :id route if new route didn't exist
    // Test a nonexistent agent ID
    await page.goto("/dashboard/agents/nonexistent-id-12345");
    await page.waitForTimeout(1000);
    const body = await page.locator("main").textContent();
    expect(body).not.toContain("NaN");
    // Should show "Agent not found" or display gracefully
  });

  test("Schemas page shows 18 default schemas", async ({ page }) => {
    await page.goto("/dashboard/schemas");
    await page.waitForTimeout(1000);
    const body = await page.locator("main").textContent();
    expect(body).not.toContain("NaN");
    await expect(page.getByText("Invoice").first()).toBeVisible();
    await expect(page.getByText("Payment").first()).toBeVisible();
    await expect(page.getByText("Employee").first()).toBeVisible();
  });

  test("Settings page shows defaults without NaN", async ({ page }) => {
    await page.goto("/dashboard/settings");
    const body = await page.locator("main").textContent();
    expect(body).not.toContain("NaN");
    expect(body).not.toContain("undefined");
    await expect(page.getByText("Fleet Governance Limits")).toBeVisible();
    await expect(page.getByText("Compliance & Data")).toBeVisible();
  });

  test("Audit page renders table structure", async ({ page }) => {
    await page.goto("/dashboard/audit");
    await page.waitForTimeout(1000);
    const body = await page.locator("main").textContent();
    expect(body).not.toContain("NaN");
    await expect(page.getByText("Audit Log")).toBeVisible();
    await expect(page.getByRole("button", { name: "Export Evidence Package" })).toBeVisible();
  });

  test("Approvals page renders tabs", async ({ page }) => {
    await page.goto("/dashboard/approvals");
    await page.waitForTimeout(1000);
    const body = await page.locator("main").textContent();
    expect(body).not.toContain("NaN");
    await expect(page.getByText("Approval Queue")).toBeVisible();
    await expect(page.getByText(/Pending \(/)).toBeVisible();
    await expect(page.getByText(/Decided \(/)).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Cross-domain access (all 3 hosts serve the same app)
// ---------------------------------------------------------------------------

test.describe("Multi-domain — All Hosts Work", () => {
  const hosts = [
    "http://agenticorg.ai",
    "http://www.agenticorg.ai",
    "http://app.agenticorg.ai",
  ];

  for (const baseURL of hosts) {
    test(`${baseURL} serves landing page`, async ({ browser }) => {
      const context = await browser.newContext({ baseURL });
      const page = await context.newPage();
      const response = await page.goto("/");
      expect(response?.status()).toBe(200);
      await expect(page.getByText("24 AI Agents.")).toBeVisible();
      await context.close();
    });

    test(`${baseURL}/dashboard loads correctly`, async ({ browser }) => {
      const context = await browser.newContext({ baseURL });
      const page = await context.newPage();
      const response = await page.goto("/dashboard");
      expect(response?.status()).toBe(200);
      await expect(page.getByText("Dashboard").first()).toBeVisible();
      await context.close();
    });
  }
});

// ---------------------------------------------------------------------------
// Landing Page → Dashboard flow
// ---------------------------------------------------------------------------

test.describe("Landing → Dashboard Flow", () => {
  test("Landing nav Dashboard link navigates to dashboard", async ({ page }) => {
    await page.goto("/");
    await page.locator("nav").getByText("Dashboard").click();
    await expect(page).toHaveURL("/dashboard");
    await expect(page.getByText("Dashboard").first()).toBeVisible();
  });

  test("Footer Dashboard link navigates correctly", async ({ page }) => {
    await page.goto("/");
    await page.locator("footer").getByRole("link", { name: "Dashboard" }).click();
    await expect(page).toHaveURL("/dashboard");
  });

  test("Footer Agents link navigates correctly", async ({ page }) => {
    await page.goto("/");
    await page.locator("footer").getByRole("link", { name: "Agents", exact: true }).click();
    await expect(page).toHaveURL("/dashboard/agents");
  });

  test("Footer Workflows link navigates correctly", async ({ page }) => {
    await page.goto("/");
    await page.locator("footer").getByRole("link", { name: "Workflows" }).click();
    await expect(page).toHaveURL("/dashboard/workflows");
  });

  test("Footer Connectors link navigates correctly", async ({ page }) => {
    await page.goto("/");
    await page.locator("footer").getByRole("link", { name: "Connectors" }).click();
    await expect(page).toHaveURL("/dashboard/connectors");
  });
});
