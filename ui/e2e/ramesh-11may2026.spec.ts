import { expect, Page, test } from "@playwright/test";

const zohoTools = [
  "create_invoice",
  "list_invoices",
  "list_overdue_invoices",
  "record_expense",
  "get_balance_sheet",
  "get_profit_loss",
  "get_ledger_balance",
  "get_trial_balance",
  "generate_gst_report",
  "calculate_tds",
  "list_chartofaccounts",
  "fetch_bank_statement",
  "check_account_balance",
  "get_transaction_list",
  "reconcile_bank",
  "reconcile_transaction",
  "get_organization",
];

async function installRoutes(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        json: {
          email: "qa@example.com",
          name: "QA",
          role: "admin",
          domain: "all",
          tenant_id: "tenant-1",
          onboarding_complete: true,
        },
      });
      return;
    }

    if (path.endsWith("/product-facts")) {
      await route.fulfill({
        json: { version: "test", connector_count: 1, agent_count: 1, tool_count: 17 },
      });
      return;
    }

    if (path.endsWith("/connectors/zoho-1/health")) {
      await route.fulfill({
        json: {
          connector_id: "zoho-1",
          name: "zoho_books",
          status: "healthy",
          healthy: true,
          tested: true,
          health: { status: "healthy", organizations: 1 },
          health_check_at: "2026-05-11T00:00:00Z",
        },
      });
      return;
    }

    if (path.endsWith("/connectors/zoho-1/test")) {
      await route.fulfill({
        json: {
          tested: true,
          name: "zoho_books",
          health: { status: "healthy", organizations: 1 },
        },
      });
      return;
    }

    if (path.endsWith("/connectors/zoho-1")) {
      await route.fulfill({
        json: {
          connector_id: "zoho-1",
          name: "zoho_books",
          category: "finance",
          description: "Zoho Books",
          base_url: "https://books.zoho.in/api/v3",
          auth_type: "oauth2",
          tool_functions: zohoTools,
          data_schema_ref: null,
          rate_limit_rpm: 100,
          timeout_ms: 10000,
          status: "active",
          health_check_at: "2026-05-11T00:00:00Z",
          created_at: "2026-05-11T00:00:00Z",
        },
      });
      return;
    }

    if (path.endsWith("/workflows/templates")) {
      await route.fulfill({ json: { items: [], total: 0 } });
      return;
    }

    if (path.endsWith("/workflows")) {
      await route.fulfill({
        json: {
          items: [
            {
              id: "wf-bank-recon",
              name: "Acme Manufacturing - Bank Recon Daily",
              version: "1.0",
              description: "Bank reconciliation",
              domain: "finance",
              trigger_type: "schedule",
              trigger_config: { cron: "0 6 * * *" },
              is_active: true,
              created_at: "2026-05-11T00:00:00Z",
            },
          ],
          total: 1,
        },
      });
      return;
    }

    await route.fulfill({ json: {} });
  });
}

test.describe("Ramesh 11 May 2026 CA/Zoho regressions", () => {
  test("Zoho connector detail shows normalized India URL, real tools, and Zoho token URL", async ({ page, baseURL }) => {
    await installRoutes(page);

    await page.goto(`${baseURL}/dashboard/connectors/zoho-1`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("main")).toContainText("https://books.zoho.in/api/v3");
    await expect(page.locator("main")).toContainText("Registered Tools (17)");
    await expect(page.locator("main")).toContainText("get_trial_balance");
    await expect(page.locator("main")).toContainText("reconcile_transaction");

    await page.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByPlaceholder("https://accounts.zoho.in/oauth/v2/token")).toBeVisible();

    await page.getByRole("button", { name: "Health Check" }).click();
    await expect(page.locator("main")).toContainText("Health: healthy (healthy)");
  });

  test("CA workflows surface scheduled triggers instead of all manual", async ({ page, baseURL }) => {
    await installRoutes(page);

    await page.goto(`${baseURL}/dashboard/workflows`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("main")).toContainText("Acme Manufacturing - Bank Recon Daily");
    await expect(page.locator("main")).toContainText("Trigger:");
    await expect(page.locator("main")).toContainText("schedule");
  });
});
