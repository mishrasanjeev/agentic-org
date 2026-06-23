import { expect, Page, test } from "@playwright/test";
import { setSessionToken } from "./helpers/auth";

async function installCommonRoutes(page: Page) {
  await setSessionToken(
    page,
    process.env.E2E_TOKEN || "aishwarya-23jun2026-session",
  );

  await page.route("**/api/v1/auth/me", async (route) => {
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
  });
  await page.route("**/api/v1/product-facts", async (route) => {
    await route.fulfill({
      json: { version: "test", connector_count: 10, agent_count: 1, tool_count: 20 },
    });
  });
  await page.route("**/api/v1/companies**", async (route) => {
    await route.fulfill({ json: { items: [], total: 0, page: 1, per_page: 20, pages: 1 } });
  });
}

function cmoPayload() {
  const scopes = [
    "crm.objects.contacts.read",
    "crm.objects.deals.read",
    "crm.objects.companies.read",
    "crm.objects.owners.read",
    "automation",
  ];
  return {
    demo: false,
    company_id: "comp-1",
    agent_count: 1,
    total_tasks_30d: 10,
    success_rate: 90,
    hitl_interventions: 0,
    total_cost_usd: 1.5,
    domain_breakdown: [
      { domain: "marketing", total: 10, completed: 9, failed: 1, avg_confidence: 0.9 },
    ],
    connector_contracts: [
      {
        connector_key: "hubspot",
        name: "HubSpot",
        category: "CRM",
        configured_status: "configured",
        vendor_id: null,
        account_id: "portal-123",
        workspace_id: null,
        read_capabilities: ["contacts.read", "deals.read", "lists.read"],
        write_capabilities: ["workflow.enroll"],
        required_read_scopes: ["crm.objects.contacts.read", "crm.objects.deals.read"],
        required_write_scopes: ["automation"],
        granted_scopes: scopes,
        missing_read_scopes: [],
        missing_write_scopes: [],
        read_scope_evidence: ["HubSpot CRM read tools registered"],
        auth_status: "valid",
        health_status: "healthy",
        contract_state: "healthy",
        read_status: "ready",
        write_status: "ready",
        read_ready: true,
        write_ready: true,
        production_ready: true,
        mock_or_test_double: false,
        last_sync_at: "2026-06-23T00:00:00Z",
        source_objects: [],
        data_freshness: {
          status: "fresh",
          ttl_seconds: 86400,
          last_sync_at: "2026-06-23T00:00:00Z",
        },
        retry_budget: {
          max_attempts: 1,
          attempts_used: 0,
          remaining_attempts: 1,
          reset_at: null,
          next_retry_at: null,
          idempotency_key: null,
          idempotency_supported: true,
        },
        degraded_mode_reason: null,
        idempotency_key_supported: true,
        external_write_confirmation_status: "none",
        external_write_confirmations: [],
        next_action_cta: "none",
      },
    ],
    connector_contract_summary: {
      total: 1,
      configured: 1,
      read_ready: 1,
      write_ready: 1,
      blocked: 0,
      degraded: 0,
      missing_write_scope: 0,
      write_unconfirmed: 0,
      write_confirmed: 0,
      mock_or_test_double: 0,
      readiness: "ready",
    },
  };
}

function workflowPayload() {
  return {
    id: "wf-23",
    name: "HubSpot approval workflow",
    version: "1.0",
    description: "Fetch HubSpot contacts after approval",
    domain: "marketing",
    trigger_type: "manual",
    trigger_config: {},
    is_active: true,
    created_at: "2026-06-23T00:00:00Z",
    definition: {
      steps: [
        { id: "approval", type: "human_in_loop" },
        { id: "retrieve_hubspot_contacts", type: "agent", agent: "crm_intelligence" },
        { id: "notify_owner", type: "notify" },
      ],
    },
  };
}

test.describe("Aishwarya 23 June 2026 workbook regressions", () => {
  test("CMO connector contracts render healthy HubSpot as read and write ready", async ({
    page,
    baseURL,
  }) => {
    await installCommonRoutes(page);
    await page.route("**/api/v1/kpis/cmo**", async (route) => {
      await route.fulfill({ json: cmoPayload() });
    });

    await page.goto(`${baseURL}/dashboard/cmo`, { waitUntil: "domcontentloaded" });

    const hubspotRow = page.locator("tr", { hasText: "HubSpot" }).first();
    await expect(hubspotRow.locator("td").nth(1)).toContainText("Healthy");
    await expect(hubspotRow.locator("td").nth(2)).toContainText("Ready");
    await expect(hubspotRow.locator("td").nth(3)).toContainText("Ready");
    await expect(hubspotRow).not.toContainText("Missing Scope");
    await expect(hubspotRow).not.toContainText("crm.objects.contacts.read");
    await expect(hubspotRow).not.toContainText("crm.objects.deals.read");
    await expect(hubspotRow).not.toContainText("automation");
  });

  test("workflow detail page shows and polls latest run execution progress", async ({
    page,
    baseURL,
  }) => {
    await installCommonRoutes(page);
    await page.route("**/api/v1/workflows/wf-23", async (route) => {
      await route.fulfill({ json: workflowPayload() });
    });

    let runCalls = 0;
    await page.route("**/api/v1/workflows/wf-23/runs**", async (route) => {
      runCalls += 1;
      await route.fulfill({
        json: {
          items: [
            runCalls < 3
              ? {
                  run_id: "run-23",
                  workflow_def_id: "wf-23",
                  status: "waiting_hitl",
                  steps_total: 3,
                  steps_completed: 1,
                  started_at: "2026-06-23T00:00:00Z",
                }
              : {
                  run_id: "run-23",
                  workflow_def_id: "wf-23",
                  status: "completed",
                  steps_total: 3,
                  steps_completed: 3,
                  started_at: "2026-06-23T00:00:00Z",
                  completed_at: "2026-06-23T00:01:00Z",
                },
          ],
          total: 1,
          page: 1,
          per_page: 5,
          pages: 1,
        },
      });
    });

    await page.goto(`${baseURL}/dashboard/workflows/wf-23`, {
      waitUntil: "domcontentloaded",
    });

    await expect(page.locator("main")).toContainText("Latest Run");
    await expect(page.locator("main")).toContainText("waiting_hitl");
    await expect(page.locator("main")).toContainText("1/3");
    await expect(page.getByRole("button", { name: "View Run" })).toBeVisible();
    await expect.poll(() => runCalls, { timeout: 10000 }).toBeGreaterThan(1);
    await expect(page.locator("main")).toContainText("completed");
    await expect(page.locator("main")).toContainText("3/3");
  });
});
