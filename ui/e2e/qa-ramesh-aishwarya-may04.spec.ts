import { expect, test } from "@playwright/test";

const user = {
  email: "aishwarya@agenticorg.ai",
  name: "Aishwarya",
  role: "admin",
  domain: "finance",
  tenant_id: "11111111-1111-1111-1111-111111111111",
  onboarding_complete: true,
};

async function mockAuthenticatedApp(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user) });
  });
}

async function mockPartnerDashboard(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/partner-dashboard", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        total_clients: 2,
        active_clients: 1,
        avg_health_score: 75,
        total_pending_filings: 2,
        total_overdue: 3,
        revenue_per_month_inr: 4999,
        clients: [
          {
            id: "company-active",
            name: "Active CA Client",
            health_score: 75,
            pending_filings: 2,
            overdue_filings: 1,
            is_active: true,
            status: "active",
            subscription_status: "trial",
          },
          {
            id: "company-inactive",
            name: "Archived CA Client",
            health_score: 50,
            pending_filings: 0,
            overdue_filings: 2,
            is_active: false,
            status: "inactive",
            subscription_status: "trial",
          },
        ],
        upcoming_deadlines: [],
      }),
    });
  });
}

test("Partner dashboard labels overdue values as filings and reflects inactive clients", async ({ page }) => {
  await mockAuthenticatedApp(page);
  await mockPartnerDashboard(page);

  await page.goto("/dashboard/partner");

  await expect(page.getByRole("heading", { name: "Partner Dashboard" })).toBeVisible();
  await expect(page.getByText("Overdue Filings").first()).toBeVisible();
  await expect(page.getByText("3 filings")).toBeVisible();
  await expect(page.getByText("Archived CA Client")).toBeVisible();
  await expect(page.getByText("Inactive")).toBeVisible();
  await expect(page.getByText("Avg Health Score")).toBeVisible();
  await expect(page.getByText("75%").first()).toBeVisible();
  await expect(page.getByText("Overdue (score < 50)")).toHaveCount(0);
});

test("Hindi language switch reaches partner dashboard metric labels", async ({ page }) => {
  await mockAuthenticatedApp(page);
  await mockPartnerDashboard(page);
  await page.addInitScript(() => localStorage.setItem("agenticorg_lang", "hi"));

  await page.goto("/dashboard/partner");

  await expect(page.getByRole("heading", { name: "पार्टनर डैशबोर्ड" })).toBeVisible();
  await expect(page.getByText("अतिदेय फाइलिंग").first()).toBeVisible();
  await expect(page.getByText("निष्क्रिय")).toBeVisible();
});

test("OAuth2 connector setup starts backend authorization and never asks for refresh-token paste", async ({ page }) => {
  await mockAuthenticatedApp(page);
  await page.route("https://accounts.google.com/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/html", body: "<h1>Google OAuth</h1>" });
  });
  let initiateBody: any = null;
  await page.route("**/api/v1/connectors/oauth/initiate", async (route) => {
    initiateBody = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?state=opaque-state",
        state: "opaque-state",
        redirect_uri: "http://localhost:4173/api/v1/oauth/callback",
        expires_in: 600,
      }),
    });
  });

  await page.goto("/dashboard/connectors/new");
  await page.locator("select").filter({ has: page.locator('option[value="oauth2"]') }).selectOption("oauth2");
  await page.getByPlaceholder("e.g. Slack, SAP S/4HANA").fill("gmail");
  await page.getByPlaceholder("Enter client ID").fill("client-id");
  await page.getByPlaceholder("Enter client secret").fill("client-secret");

  await expect(page.getByPlaceholder(/refresh token/i)).toHaveCount(0);
  await page.getByRole("button", { name: "Authorize Connector" }).click();
  await expect(page.getByRole("heading", { name: "Google OAuth" })).toBeVisible();

  expect(initiateBody).toMatchObject({
    connector_name: "gmail",
    client_id: "client-id",
    client_secret: "client-secret",
    category: "finance",
  });
});

test("CA firms trial request posts firm metadata and reports confirmation email", async ({ page }) => {
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
  await page.getByRole("button", { name: /start free trial/i }).first().click();
  await page.getByRole("textbox", { name: /^Name \*/ }).fill("Aishwarya");
  await page.getByLabel(/Work Email/).fill("aishwarya@agenticorg.ai");
  await page.getByLabel(/Firm Name/).fill("Aishwarya CA LLP");
  await page.getByLabel(/Number of Clients/).selectOption("6-15");
  await page.getByRole("dialog").getByRole("button", { name: "Start Free Trial" }).click();

  await expect(page.getByText(/We sent a confirmation email/)).toBeVisible();
  expect(demoBody).toMatchObject({
    name: "Aishwarya",
    email: "aishwarya@agenticorg.ai",
    firm: "Aishwarya CA LLP",
    clients: "6-15",
    source: "ca-firms-solution",
  });
});
