import { expect, test } from "./helpers/test";
import { authenticate } from "./helpers/auth";

// The Partner dashboard is retired: /dashboard/partner opens the Insights partner
// view. The mock gives the partner view the figures the old page showed.
async function mockPartnerDashboard(page: import("@playwright/test").Page) {
  const period = { from: "2026-05-01", to: "2026-05-04", granularity: "day" };
  const metric = (key: string, title: string, value: number, unit = "count") => ({
    key,
    title,
    definition: `${title} for the firm's client companies.`,
    unit,
    source: "fixture",
    data_quality: "measured",
    computed_at: "2026-05-04T00:00:00Z",
    period,
    value,
    state: "ok",
    series: [],
    rows: [],
  });
  await page.route("**/api/v1/insights/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        enabled: true,
        views: ["executive", "finance", "partner"],
        platform_operator: false,
        settings: { timezone: "Asia/Kolkata", fiscal_year_start_month: 4, currency: "INR" },
      }),
    });
  });
  await page.route("**/api/v1/insights/views/partner**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        view: "partner",
        period,
        tiles: [
          // One active client; the archived one is not counted.
          metric("partner.clients", "Active client companies", 1),
          // Three overdue statutory deadlines across the clients.
          metric("backoffice.deadlines", "Compliance deadlines", 3),
          metric("partner.filings", "Filings waiting for approval", 2),
          metric("partner.billing", "Invoiced to clients", 4999, "inr"),
        ],
        charts: [],
        needs_you: [],
      }),
    });
  });
}

test("Partner dashboard URL opens the Insights partner view with overdue deadlines and active clients", async ({ page }) => {
  await authenticate(page);
  await mockPartnerDashboard(page);

  await page.goto("/dashboard/partner");

  await expect(page).toHaveURL(/\/dashboard\/insights\?(.*&)?view=partner/);
  await expect(page.getByRole("heading", { name: "Insights", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Partner" })).toHaveAttribute("aria-selected", "true");
  const deadlines = page.locator('[data-metric-key="backoffice.deadlines"]');
  await expect(deadlines).toContainText("Compliance deadlines");
  await expect(deadlines.getByTestId("tile-value")).toHaveText("3");
  const clients = page.locator('[data-metric-key="partner.clients"]');
  await expect(clients).toContainText("Active client companies");
  await expect(clients.getByTestId("tile-value")).toHaveText("1");
  await expect(page.getByRole("heading", { name: "Partner Dashboard" })).toHaveCount(0);
});

test("Hindi language switch reaches the Insights partner view", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("agenticorg_lang", "hi"));
  await authenticate(page);
  await mockPartnerDashboard(page);

  await page.goto("/dashboard/partner");

  await expect(page).toHaveURL(/\/dashboard\/insights\?(.*&)?view=partner/);
  await expect(page.getByRole("heading", { name: "इनसाइट्स", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "पार्टनर" })).toHaveAttribute("aria-selected", "true");
});

test("OAuth2 connector setup registers internally without external authorization", async ({ page }) => {
  await authenticate(page);
  let createBody: any = null;
  await page.route("**/api/v1/connectors", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    createBody = route.request().postDataJSON();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        id: "connector-1",
        connector_id: "connector-1",
        name: "gmail",
        status: "active",
      }),
    });
  });

  await page.goto("/dashboard/connectors/new");
  await page.locator("select").filter({ has: page.locator('option[value="oauth2"]') }).selectOption("oauth2");
  await page.getByPlaceholder(/zoho_books/).fill("gmail");
  await page.getByPlaceholder("Enter client ID").fill("client-id");
  await page.getByPlaceholder("Enter client secret").fill("client-secret");

  await expect(page.getByPlaceholder(/refresh token/i)).toHaveCount(0);
  await page.getByRole("button", { name: "Register Connector" }).click();

  expect(createBody).toMatchObject({
    name: "gmail",
    auth_type: "oauth2",
    category: "finance",
    auth_config: {
      client_id: "client-id",
      client_secret: "client-secret",
    },
  });
});

test("CA firms evaluation request posts firm metadata and reports confirmation email", async ({ page }) => {
  let demoBody: any = null;
  await page.route("**/api/v1/demo-request", async (route) => {
    demoBody = route.request().postDataJSON();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        status: "received",
        email: { requester_confirmation_sent: true },
      }),
    });
  });

  await page.goto("/solutions/ca-firms");
  await page.getByRole("button", { name: /request ca pack evaluation/i }).first().click();
  await page.getByRole("textbox", { name: /^Name \*/ }).fill("Aishwarya");
  await page.getByLabel(/Work Email/).fill("aishwarya@agenticorg.ai");
  await page.getByLabel(/Firm Name/).fill("Aishwarya CA LLP");
  await page.getByLabel(/Number of Clients/).selectOption("6-15");
  await page.getByRole("dialog").getByRole("button", { name: "Request Evaluation" }).click();

  await expect(page.getByText(/We sent a confirmation email/)).toBeVisible();
  expect(demoBody).toMatchObject({
    name: "Aishwarya",
    email: "aishwarya@agenticorg.ai",
    firm: "Aishwarya CA LLP",
    clients: "6-15",
    source: "ca-firms-solution",
  });
});
