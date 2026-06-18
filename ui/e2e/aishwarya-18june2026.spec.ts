import { expect, Page, test } from "@playwright/test";

async function installCommonRoutes(page: Page) {
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
}

test.describe("Aishwarya 18 June 2026 connector reopen regressions", () => {
  test("GA4 connector test shows actionable 403 remediation", async ({ page, baseURL }) => {
    await installCommonRoutes(page);
    await page.route("**/api/v1/connectors/ga4-id", async (route) => {
      await route.fulfill({
        json: {
          id: "ga4-id",
          connector_id: "ga4-id",
          name: "ga4",
          category: "marketing",
          description: "",
          base_url: "https://analyticsdata.googleapis.com/v1beta",
          auth_type: "oauth2",
          has_credentials: true,
          tool_functions: ["run_report", "get_metadata"],
          data_schema_ref: "",
          rate_limit_rpm: 200,
          timeout_ms: 10000,
          status: "error",
          health_check_at: null,
          created_at: "2026-06-18T00:00:00Z",
        },
      });
    });
    await page.route("**/api/v1/connectors/ga4-id/test", async (route) => {
      await route.fulfill({
        json: {
          tested: true,
          name: "ga4",
          health: {
            status: "unhealthy",
            http_status: 403,
            error: "ga4_permission_denied",
            reason: "GA4 permission denied for the property metadata endpoint.",
            remediation: "Grant the authorizing user Viewer access and reconnect.",
            required_scopes: ["https://www.googleapis.com/auth/analytics.readonly"],
          },
        },
      });
    });

    await page.goto(`${baseURL}/dashboard/connectors/ga4-id`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Test Connection" }).click();

    await expect(page.locator("main")).toContainText("HTTP 403");
    await expect(page.locator("main")).toContainText("Viewer access");
    await expect(page.locator("main")).toContainText("analytics.readonly");
  });

  test("shadow sample generation reports count update with accuracy pending", async ({ page, baseURL }) => {
    await installCommonRoutes(page);
    let agent = {
      id: "agent-shadow",
      name: "Shadow Agent",
      employee_name: "Shadow Agent",
      agent_type: "crm_intelligence_agent",
      domain: "marketing",
      status: "shadow",
      version: "1.0.0",
      confidence_floor: 0.8,
      shadow_sample_count: 0,
      shadow_accuracy_current: null,
      shadow_accuracy_floor: 0.8,
      shadow_min_samples: 2,
      authorized_tools: ["list_contacts", "list_deals"],
      created_at: "2026-06-18T00:00:00Z",
    };
    await page.route("**/api/v1/agents/agent-shadow", async (route) => {
      await route.fulfill({ json: agent });
    });
    await page.route("**/api/v1/agents/agent-shadow/run", async (route) => {
      agent = { ...agent, shadow_sample_count: 1 };
      await route.fulfill({
        json: {
          run_id: "run-shadow",
          task_id: "run-shadow",
          agent_id: "agent-shadow",
          status: "completed",
          confidence: null,
          output: { answer: "Sample generated." },
          reasoning_trace: [],
          tool_calls: [],
          performance: { total_latency_ms: 5, llm_tokens_used: 0, llm_cost_usd: 0 },
          hitl_trigger: null,
          error: null,
          shadow_metrics: {
            sample_counted: true,
            accuracy_updated: false,
            sample_count_delta: 1,
            reason: "sample_counted_accuracy_pending",
          },
        },
      });
    });
    await page.route("**/api/v1/agents/agent-shadow/feedback", async (route) => {
      await route.fulfill({ json: { feedback: [], count: 0 } });
    });
    await page.route("**/api/v1/agents/agent-shadow/explanation/latest", async (route) => {
      await route.fulfill({ json: { has_run: false, bullets: [], tools_cited: [] } });
    });

    await page.goto(`${baseURL}/dashboard/agents/agent-shadow`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "shadow" }).click();
    await page.getByRole("button", { name: "Generate Test Sample" }).click();

    await expect(page.locator("main")).toContainText("Count updated; accuracy pending");
    await expect(page.locator("main")).toContainText("Sample count (1/2)");
  });

  test("CMO dashboard marks healthy HubSpot CRM read contract ready", async ({ page, baseURL }) => {
    await installCommonRoutes(page);
    await page.route("**/api/v1/kpis/cmo**", async (route) => {
      await route.fulfill({
        json: {
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
              name: "HubSpot CRM Read",
              category: "CRM",
              configured_status: "configured",
              vendor_id: null,
              account_id: "portal-123",
              workspace_id: null,
              read_capabilities: ["contacts.read", "deals.read", "lists.read"],
              write_capabilities: [],
              required_read_scopes: ["crm.objects.contacts.read", "crm.objects.deals.read"],
              required_write_scopes: [],
              granted_scopes: [],
              missing_read_scopes: [],
              missing_write_scopes: [],
              read_scope_evidence: [
                "Healthy HubSpot connector state proves CRM read capability even without a persisted OAuth scope string.",
              ],
              auth_status: "valid",
              health_status: "healthy",
              contract_state: "healthy",
              read_status: "ready",
              write_status: "read_only",
              read_ready: true,
              write_ready: false,
              production_ready: true,
              mock_or_test_double: false,
              last_sync_at: "2026-06-18T00:00:00Z",
              source_objects: [],
              data_freshness: {
                status: "fresh",
                ttl_seconds: 86400,
                last_sync_at: "2026-06-18T00:00:00Z",
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
              next_action_cta: "none",
            },
          ],
          connector_contract_summary: {
            total: 1,
            configured: 1,
            read_ready: 1,
            write_ready: 0,
            blocked: 0,
            degraded: 0,
            missing_write_scope: 0,
            write_unconfirmed: 0,
            write_confirmed: 0,
            mock_or_test_double: 0,
            readiness: "ready",
          },
        },
      });
    });

    await page.goto(`${baseURL}/dashboard/cmo`, { waitUntil: "domcontentloaded" });

    await expect(page.locator("main")).toContainText("Marketing Connector Contracts");
    await expect(page.locator("main")).toContainText("HubSpot CRM Read");
    await expect(page.locator("main")).toContainText("Ready");
    await expect(page.locator("main")).toContainText("Healthy HubSpot connector state proves CRM read capability");
    await expect(page.locator("main")).not.toContainText("Missing: crm.objects.contacts.read, crm.objects.deals.read");
  });
});
