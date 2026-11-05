import { expect, test } from "@playwright/test";

// The Partner dashboard is retired: /dashboard/partner opens the Insights
// partner view, whose tiles carry the client, deadline, filing and billing
// figures the old page showed.
const PERIOD = { from: "2099-01-01", to: "2099-01-30", granularity: "day" };

function snapshotMetric(key: string, title: string, value: number, unit = "count") {
  return {
    key,
    title,
    definition: `${title} for the firm's client companies.`,
    unit,
    source: "fixture",
    data_quality: "measured",
    computed_at: "2099-01-30T00:00:00Z",
    period: PERIOD,
    value,
    state: "ok",
    series: [],
    rows: [],
  };
}

const partnerViewFixture = {
  view: "partner",
  period: PERIOD,
  tiles: [
    // Only the active client counts; the dormant one is left out.
    snapshotMetric("partner.clients", "Active client companies", 1),
    snapshotMetric("backoffice.deadlines", "Compliance deadlines", 0),
    snapshotMetric("partner.filings", "Filings waiting for approval", 1),
    snapshotMetric("partner.billing", "Invoiced to clients", 4999, "inr"),
  ],
  charts: [],
  needs_you: [],
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
    if (path.endsWith("/insights/status")) {
      await route.fulfill({
        json: {
          enabled: true,
          views: ["executive", "finance", "partner"],
          platform_operator: false,
          settings: { timezone: "Asia/Kolkata", fiscal_year_start_month: 4, currency: "INR" },
        },
      });
      return;
    }
    if (path.endsWith("/insights/views/partner")) {
      await route.fulfill({ json: partnerViewFixture });
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

test("Aishwarya May 6 partner figures render in the Insights partner view in English and Hindi", async ({ page }) => {
  await page.goto("/dashboard/partner");

  await expect(page).toHaveURL(/\/dashboard\/insights\?(.*&)?view=partner/);
  await expect(page.getByRole("heading", { name: "Insights", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Partner" })).toHaveAttribute("aria-selected", "true");

  const clients = page.locator('[data-metric-key="partner.clients"]');
  await expect(clients).toContainText("Active client companies");
  await expect(clients.getByTestId("tile-value")).toHaveText("1");
  await expect(page.locator('[data-metric-key="backoffice.deadlines"]')).toContainText("Compliance deadlines");
  await expect(page.locator('[data-metric-key="partner.filings"]').getByTestId("tile-value")).toHaveText("1");
  await expect(page.locator('[data-metric-key="partner.billing"]')).toContainText("Invoiced to clients");
  await expect(page.getByText("Partner Dashboard")).toHaveCount(0);

  await page.getByTestId("language-picker").selectOption("hi");
  await expect(page.getByRole("heading", { name: "इनसाइट्स", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "पार्टनर" })).toHaveAttribute("aria-selected", "true");
});
