import { expect, Page, test } from "@playwright/test";
import { E2E_TOKEN, requireAuth, setSessionToken } from "./helpers/auth";

async function installCommonRoutes(page: Page) {
  requireAuth();
  await setSessionToken(page, E2E_TOKEN);
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();

    if (url.pathname === "/api/v1/auth/me") {
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
    if (url.pathname === "/api/v1/product-facts") {
      await route.fulfill({ json: { version: "test", connector_count: 3, agent_count: 1, tool_count: 4 } });
      return;
    }
    if (url.pathname === "/api/v1/companies") {
      await route.fulfill({ json: [] });
      return;
    }
    if (url.pathname === "/api/v1/connectors") {
      await route.fulfill({ json: [] });
      return;
    }
    if (url.pathname === "/api/v1/agents" && method === "GET") {
      await route.fulfill({ json: { agents: [], total: 0 } });
      return;
    }
    await route.fulfill({ json: {} });
  });
}

test.describe("2026-09-21 bug-sheet producer regressions", () => {
  test("BUG-01 shadow sample follows the visible Generate Test Sample workflow", async ({ page }) => {
    await installCommonRoutes(page);
    let sampleCount = 0;
    const agent = {
      id: "agent-shadow",
      name: "Marketing Performance Agent",
      employee_name: "Marketing Performance Agent",
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
      created_at: "2026-09-21T00:00:00Z",
    };
    await page.route("**/api/v1/agents/agent-shadow", async (route) => {
      await route.fulfill({ json: { ...agent, shadow_sample_count: sampleCount } });
    });
    await page.route("**/api/v1/agents/agent-shadow/run", async (route) => {
      sampleCount += 1;
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

    await page.goto("/dashboard/agents/agent-shadow", { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "shadow" }).click();
    await page.getByRole("button", { name: "Generate Test Sample" }).click();

    await expect(page.locator("main")).toContainText("Count updated; accuracy pending");
    await expect(page.locator("main")).toContainText("Sample count (1/2)");
  });

  test("BUG-02 Create Virtual Employee sends the description to the tenant-aware generate route", async ({ page }) => {
    await installCommonRoutes(page);
    let generated = false;
    await page.route("**/api/v1/agents/generate", async (route) => {
      generated = true;
      const body = route.request().postDataJSON() as { description?: string };
      expect(body.description).toContain("invoices");
      await route.fulfill({
        json: {
          suggestions: [
            {
              confidence: 0.95,
              agent_type: "ap_processor",
              domain: "finance",
              employee_name: "Invoice Agent",
              designation: "AP Processing Specialist",
              suggested_tools: ["fetch_bank_statement"],
              system_prompt: "Process invoices.",
              confidence_floor: 0.88,
              hitl_condition: "confidence < 0.88",
              specialization: "Invoice review",
            },
          ],
        },
      });
    });

    await page.goto("/dashboard/agents/new", { waitUntil: "domcontentloaded" });
    await page.getByTestId("nl-description").fill("Create an employee who reviews invoices");
    await page.getByTestId("generate-btn").click();

    await expect(page.getByPlaceholder("e.g. Priya, Arjun, Maya")).toHaveValue("Invoice Agent");
    expect(generated).toBe(true);
  });
});
