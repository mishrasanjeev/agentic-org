/**
 * CA Firms 2026-05-03 reopen regression coverage.
 *
 * These tests are hermetic: API routes are mocked so we can verify the
 * rendered product behavior without live Zoho/GSTN/Grantex credentials.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

import { APP, setSessionToken } from "./helpers/auth";

const AGENT_ID = "e2e-ca-agent";
const COMPANY_ID = "e2e-company";

function json(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockApi(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path.endsWith("/api/v1/auth/me")) {
      return json(route, {
        email: "qa@agenticorg.ai",
        name: "QA Admin",
        role: "admin",
        domain: "all",
        tenant_id: "58483c90-494b-445d-85c6-245a727fe372",
        onboarding_complete: true,
      });
    }

    if (path.endsWith(`/api/v1/agents/${AGENT_ID}`)) {
      return json(route, {
        id: AGENT_ID,
        name: "TDS Compliance Agent",
        employee_name: "Acme - TDS Compliance Agent",
        agent_type: "tds_compliance",
        domain: "finance",
        status: "shadow",
        version: "1.0.0",
        confidence_floor: 0.92,
        shadow_sample_count: 0,
        shadow_accuracy_current: null,
        created_at: "2026-05-03T00:00:00Z",
        authorized_tools: [
          "calculate_tds",
          "get_ledger_balance",
          "list_invoices",
          "file_form_26q",
        ],
        connector_ids: ["registry-zoho_books", "registry-income_tax_india"],
        config: {
          tool_connectors: {
            calculate_tds: "zoho_books",
            get_ledger_balance: "zoho_books",
            list_invoices: "zoho_books",
            file_form_26q: "income_tax_india",
          },
          grantex: { grantex_agent_id: "grx-agent-1" },
        },
      });
    }

    if (path.endsWith(`/api/v1/companies/${COMPANY_ID}`)) {
      return json(route, {
        id: COMPANY_ID,
        name: "Acme Pvt Ltd",
        gstin: "27ABCDE1234F1Z5",
        pan: "ABCDE1234F",
        state_code: "27",
        is_active: true,
        gst_auto_file: false,
        client_health_score: 88,
        subscription_status: "active",
        created_at: "2026-05-03T00:00:00Z",
      });
    }

    if (path.endsWith(`/api/v1/companies/${COMPANY_ID}/roles`)) {
      return json(route, { roles: [], valid_roles: ["partner", "manager"] });
    }

    if (path.startsWith(`/api/v1/companies/${COMPANY_ID}/`)) {
      return json(route, { items: [] });
    }

    if (path.endsWith("/api/v1/agents")) {
      return json(route, {
        items: [
          {
            id: AGENT_ID,
            name: "Acme - GST Filing Agent",
            employee_name: "Acme - GST Filing Agent",
            designation: "GST Filing Agent",
            domain: "finance",
            status: "shadow",
          },
        ],
        total: 1,
        page: 1,
        per_page: 200,
        pages: 1,
      });
    }

    if (path.endsWith("/api/v1/workflows") || path.endsWith("/api/v1/audit")) {
      return json(route, { items: [] });
    }

    return json(route, {});
  });

  await setSessionToken(page, "fake.e2e.session");
}

test.describe("CA Firms May 03 reopen fixes", () => {
  test("Scopes tab derives connectors and does not fabricate Grantex state", async ({
    page,
  }) => {
    await mockApi(page);
    await page.goto(`${APP}/dashboard/agents/${AGENT_ID}`, {
      waitUntil: "domcontentloaded",
    });

    await page.getByRole("button", { name: "Scopes" }).click();

    const main = page.locator("main");
    await expect(main).toContainText("tool:zoho_books:execute:list_invoices");
    await expect(main).toContainText("tool:income_tax_india:execute:file_form_26q");
    await expect(main).toContainText("No enforcement decisions recorded");
    await expect(main).not.toContainText("salesforce");
    await expect(main).not.toContainText("hubspot");
    await expect(main).not.toContainText("Expired");
    await expect(main).not.toContainText("Expiring soon");
  });

  test("Company Agents tab shows provisioned CA pack agents", async ({ page }) => {
    await mockApi(page);
    await page.goto(`${APP}/dashboard/companies/${COMPANY_ID}`, {
      waitUntil: "domcontentloaded",
    });

    await page.getByRole("button", { name: "Agents" }).click();

    const main = page.locator("main");
    await expect(main).toContainText("Acme - GST Filing Agent");
    await expect(main).not.toContainText("No CA pack agents are provisioned");
  });

  test("GST auto-file is locked until GSTN credentials exist", async ({ page }) => {
    await mockApi(page);
    await page.goto(`${APP}/dashboard/companies/${COMPANY_ID}`, {
      waitUntil: "domcontentloaded",
    });

    await page.getByRole("button", { name: "Settings" }).click();

    const main = page.locator("main");
    await expect(main).toContainText("GST auto-file is locked");
    await expect(page.getByLabel("Enable GST auto-file")).toBeDisabled();
    await expect(main).toContainText("No GSTN credentials stored for this company");
  });
});
