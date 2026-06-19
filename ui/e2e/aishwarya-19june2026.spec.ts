import { expect, Page, test } from "@playwright/test";
import { setSessionToken } from "./helpers/auth";

async function installCommonRoutes(page: Page) {
  await setSessionToken(
    page,
    process.env.E2E_TOKEN || "aishwarya-19jun2026-session",
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
        granted_scopes: ["crm.objects.contacts.read"],
        missing_read_scopes: [],
        missing_write_scopes: ["automation"],
        read_scope_evidence: [
          "Healthy HubSpot connector state proves CRM read capability even without a persisted OAuth scope string.",
        ],
        auth_status: "valid",
        health_status: "healthy",
        contract_state: "healthy",
        read_status: "ready",
        write_status: "missing_scope",
        read_ready: true,
        write_ready: false,
        production_ready: true,
        mock_or_test_double: false,
        last_sync_at: "2026-06-19T00:00:00Z",
        source_objects: [],
        data_freshness: {
          status: "fresh",
          ttl_seconds: 86400,
          last_sync_at: "2026-06-19T00:00:00Z",
        },
        retry_budget: {
          max_attempts: 0,
          attempts_used: 0,
          remaining_attempts: 0,
          reset_at: null,
          next_retry_at: null,
          idempotency_key: null,
          idempotency_supported: false,
        },
        degraded_mode_reason: null,
        idempotency_key_supported: false,
        external_write_confirmation_status: "none",
        external_write_confirmations: [],
        next_action_cta: "add_scope",
      },
    ],
    connector_contract_summary: {
      total: 1,
      configured: 1,
      read_ready: 1,
      write_ready: 0,
      blocked: 0,
      degraded: 0,
      missing_write_scope: 1,
      write_unconfirmed: 0,
      write_confirmed: 0,
      mock_or_test_double: 0,
      readiness: "ready",
    },
  };
}

test.describe("Aishwarya 19 June 2026 reopened regressions", () => {
  test("CMO connector contracts keep HubSpot read ready while write scope is missing", async ({
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
    await expect(hubspotRow.locator("td").nth(3)).toContainText("Missing Scope");
    await expect(hubspotRow.locator("td").nth(3)).toContainText("Missing write scopes: automation");
    await expect(hubspotRow.locator("td").nth(3)).not.toContainText("crm.objects.contacts.read");
    await expect(hubspotRow.locator("td").nth(3)).not.toContainText("crm.objects.deals.read");
  });

  test("workflow run keeps polling while waiting for HITL and shows resumed progress", async ({
    page,
    baseURL,
  }) => {
    await installCommonRoutes(page);
    let calls = 0;
    await page.route("**/api/v1/workflows/runs/run-hitl", async (route) => {
      calls += 1;
      await route.fulfill({
        json:
          calls <= 2
            ? {
                run_id: "run-hitl",
                workflow_def_id: "wf-1",
                status: "waiting_hitl",
                steps_total: 2,
                steps_completed: 1,
                started_at: "2026-06-19T00:00:00Z",
                steps: [
                  {
                    step_id: "approval",
                    step_type: "human_in_loop",
                    status: "waiting_hitl",
                  },
                ],
              }
            : {
                run_id: "run-hitl",
                workflow_def_id: "wf-1",
                status: "completed",
                steps_total: 2,
                steps_completed: 2,
                started_at: "2026-06-19T00:00:00Z",
                completed_at: "2026-06-19T00:01:00Z",
                steps: [
                  {
                    step_id: "approval",
                    step_type: "human_in_loop",
                    status: "completed",
                  },
                  {
                    step_id: "fetch_hubspot_contacts",
                    step_type: "connector_tool",
                    status: "completed",
                  },
                ],
              },
      });
    });

    await page.goto(`${baseURL}/dashboard/workflows/wf-1/runs/run-hitl`, {
      waitUntil: "domcontentloaded",
    });

    await expect(page.locator("main")).toContainText("waiting_hitl");
    await expect.poll(() => calls, { timeout: 10000 }).toBeGreaterThan(2);
    await expect(page.locator("main")).toContainText("completed");
    await expect(page.locator("main")).toContainText("2/2");
    await expect(page.locator("main")).toContainText("fetch_hubspot_contacts");
  });

  test("workflow run renders structured failed step details", async ({ page, baseURL }) => {
    await installCommonRoutes(page);
    await page.route("**/api/v1/workflows/runs/run-failed", async (route) => {
      await route.fulfill({
        json: {
          run_id: "run-failed",
          workflow_def_id: "wf-1",
          status: "failed",
          steps_total: 1,
          steps_completed: 1,
          started_at: "2026-06-19T00:00:00Z",
          completed_at: "2026-06-19T00:01:00Z",
          steps: [
            {
              step_id: "fetch_hubspot_contacts",
              step_type: "connector_tool",
              status: "failed",
              error: {
                code: "connector_tool_execution_failed",
                message: "HubSpot returned 403 insufficient scope",
                details: { connector: "hubspot", tool: "list_contacts", http_status: 403 },
              },
              error_code: "connector_tool_execution_failed",
              error_message: "HubSpot returned 403 insufficient scope",
            },
          ],
        },
      });
    });

    await page.goto(`${baseURL}/dashboard/workflows/wf-1/runs/run-failed`, {
      waitUntil: "domcontentloaded",
    });

    const error = page.getByTestId("step-error-fetch_hubspot_contacts");
    await expect(error).toContainText("connector_tool_execution_failed");
    await expect(error).toContainText("HubSpot returned 403 insufficient scope");
    await expect(error).toContainText("list_contacts");
    await expect(error).not.toContainText("[object Object]");
  });
});
