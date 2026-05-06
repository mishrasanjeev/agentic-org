import { expect, test } from "@playwright/test";

const dashboardFixture = {
  total_clients: 2,
  active_clients: 1,
  inactive_clients: 1,
  metrics_scope: "active_clients_only",
  avg_health_score: 75,
  total_pending_filings: 1,
  total_overdue: 0,
  revenue_per_month_inr: 4999,
  clients: [
    {
      id: "active-co",
      name: "Active Retail Pvt Ltd",
      health_score: 75,
      pending_filings: 1,
      overdue_filings: 0,
      is_active: true,
      status: "active",
      subscription_status: "trial",
      trial_days_remaining: 5,
      metrics_included: true,
    },
    {
      id: "inactive-co",
      name: "Dormant LLP",
      health_score: null,
      pending_filings: 9,
      overdue_filings: 3,
      is_active: false,
      status: "inactive",
      subscription_status: "trial",
      trial_days_remaining: 5,
      metrics_included: false,
    },
  ],
  upcoming_deadlines: [
    {
      id: "deadline-20",
      company_id: "active-co",
      deadline_type: "GSTR-3B",
      filing_period: "Apr 2026",
      company_name: "Active Retail Pvt Ltd",
      due_date: "2099-01-20",
      days_remaining: 20,
      company_status: "active",
    },
    {
      id: "deadline-3",
      company_id: "active-co",
      deadline_type: "TDS 26Q",
      filing_period: "Q4 FY26",
      company_name: "Active Retail Pvt Ltd",
      due_date: "2099-01-03",
      days_remaining: 3,
      company_status: "active",
    },
    {
      id: "inactive-deadline",
      company_id: "inactive-co",
      deadline_type: "Dormant GST",
      filing_period: "Apr 2026",
      company_name: "Dormant LLP",
      due_date: "2099-01-04",
      days_remaining: 4,
      company_status: "inactive",
    },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        json: {
          email: "admin@agenticorg.ai",
          name: "Admin",
          role: "admin",
          domain: "all",
          tenant_id: "tenant-001",
          onboarding_complete: true,
        },
      });
      return;
    }
    if (path.endsWith("/partner-dashboard")) {
      await route.fulfill({ json: dashboardFixture });
      return;
    }
    if (path.endsWith("/companies")) {
      await route.fulfill({
        json: {
          items: [{ id: "active-co", name: "Active Retail Pvt Ltd" }],
          total: 1,
          page: 1,
          per_page: 50,
        },
      });
      return;
    }
    await route.fulfill({ json: { items: [], total: 0 } });
  });
});

test("Aishwarya May 6 dashboard fixes render correctly in English and Hindi", async ({ page }) => {
  await page.goto("/dashboard/partner");

  await expect(page.getByRole("heading", { name: "Partner Dashboard" })).toBeVisible();
  await expect(page.getByText("Trial - 5 days left").first()).toBeVisible();
  await expect(page.getByText("N/A")).toBeVisible();
  await expect(page.getByText("Active clients with no pending filings").first()).toBeVisible();
  await expect(page.getByText("20 days left")).toBeVisible();
  await expect(page.getByText("3 days left")).toBeVisible();
  await expect(page.getByText("Dormant GST - Apr 2026")).toHaveCount(0);

  await page.getByTestId("language-picker").selectOption("hi");
  await expect(page.getByText("ट्रायल - 5 दिन शेष").first()).toBeVisible();
  await expect(page.getByText("बिना लंबित फाइलिंग वाले सक्रिय क्लाइंट").first()).toBeVisible();
});
