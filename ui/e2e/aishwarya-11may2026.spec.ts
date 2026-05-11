import { expect, Page, test } from "@playwright/test";

type MockAgent = {
  id: string;
  name: string;
  employee_name: string;
  agent_type: string;
  domain: string;
  status: string;
  version: string;
  confidence_floor: number;
  shadow_sample_count: number;
  shadow_accuracy_current: number;
  shadow_accuracy_floor: number;
  shadow_min_samples: number;
  authorized_tools: string[];
  created_at: string;
};

function buildAgents(count: number): MockAgent[] {
  return Array.from({ length: count }, (_, i) => {
    const n = i + 1;
    return {
      id: n === 1 ? "agent-1" : `agent-${n}`,
      name: `Agent ${n}`,
      employee_name: `Agent ${n}`,
      agent_type: "tax_compliance",
      domain: n % 2 === 0 ? "finance" : "ops",
      status: n % 3 === 0 ? "shadow" : "active",
      version: n === 1 ? "1.0.1" : "1.0.0",
      confidence_floor: 0.88,
      shadow_sample_count: 12,
      shadow_accuracy_current: 0.82,
      shadow_accuracy_floor: 0.8,
      shadow_min_samples: 10,
      authorized_tools: ["calculate_tds"],
      created_at: "2026-05-11T00:00:00Z",
    };
  });
}

async function installRoutes(page: Page, agents: MockAgent[]) {
  let detailAgent = { ...agents[0], status: "active", version: "1.0.1" };

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
        json: {
          version: "test",
          connector_count: 10,
          agent_count: agents.length,
          tool_count: 20,
        },
      });
      return;
    }

    if (path.endsWith("/agents/agent-1/run") && request.method() === "POST") {
      await route.fulfill({
        json: {
          run_id: "run-1",
          task_id: "run-1",
          agent_id: "agent-1",
          status: "completed",
          confidence: 0.91,
          output: { answer: "Generated compliance summary" },
          reasoning_trace: ["validated task"],
          tool_calls: [],
          performance: { total_latency_ms: 10, llm_tokens_used: 0, llm_cost_usd: 0 },
          hitl_trigger: null,
          error: null,
        },
      });
      return;
    }

    if (path.endsWith("/agents/agent-1/rollback") && request.method() === "POST") {
      detailAgent = { ...detailAgent, status: "shadow", version: "1.0.0" };
      await route.fulfill({
        json: {
          id: "agent-1",
          rolled_back: true,
          from_status: "active",
          to_status: "shadow",
          from_version: "1.0.1",
          to_version: "1.0.0",
        },
      });
      return;
    }

    if (path.endsWith("/agents/agent-1") && request.method() === "GET") {
      await route.fulfill({ json: detailAgent });
      return;
    }

    if (path.endsWith("/agents") && request.method() === "GET") {
      const pageNum = Number(url.searchParams.get("page") || "1");
      const perPage = Number(url.searchParams.get("per_page") || "20");
      const domain = url.searchParams.get("domain");
      const status = url.searchParams.get("status");
      let filtered = agents;
      if (domain) filtered = filtered.filter((agent) => agent.domain === domain);
      if (status) filtered = filtered.filter((agent) => agent.status === status);
      const start = (pageNum - 1) * perPage;
      const items = filtered.slice(start, start + perPage);
      await route.fulfill({
        json: {
          items,
          total: filtered.length,
          page: pageNum,
          per_page: perPage,
          pages: Math.max(1, Math.ceil(filtered.length / perPage)),
        },
      });
      return;
    }

    if (path.endsWith("/approvals")) {
      await route.fulfill({ json: { items: [], total: 0, page: 1, per_page: 20, pages: 1 } });
      return;
    }

    if (path.endsWith("/audit")) {
      await route.fulfill({ json: { items: [], total: 0, page: 1, per_page: 10, pages: 1 } });
      return;
    }

    if (path.endsWith("/agents/agent-1/feedback")) {
      await route.fulfill({ json: { feedback: [], count: 0 } });
      return;
    }

    if (path.endsWith("/agents/agent-1/explanation/latest")) {
      await route.fulfill({ json: { has_run: false, bullets: [], tools_cited: [] } });
      return;
    }

    await route.fulfill({ json: {} });
  });
}

test.describe("Aishwarya 11 May 2026 regressions", () => {
  test("dashboard and agent fleet load every agent page, not just the first 20", async ({ page, baseURL }) => {
    const agents = buildAgents(125);
    await installRoutes(page, agents);

    await page.goto(`${baseURL}/dashboard`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("main")).toContainText("Total Agents");
    await expect(page.locator("main")).toContainText("125");

    await page.goto(`${baseURL}/dashboard/agents`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("main")).toContainText("Agent Fleet");
    await expect(page.locator("main")).toContainText("Agent 125");
  });

  test("run dialog rejects meaningless prompts and renders successful output", async ({ page, baseURL }) => {
    const agents = buildAgents(25);
    await installRoutes(page, agents);

    await page.goto(`${baseURL}/dashboard/agents/agent-1`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Run Agent" }).click();
    await page.getByPlaceholder("What should the agent do?").fill("@@@@@");
    await expect(page.getByRole("button", { name: /^Run$/ })).toBeDisabled();
    await expect(page.locator("main")).toContainText("Please enter a valid task or prompt.");

    await page.getByPlaceholder("What should the agent do?").fill("Prepare TDS filing summary");
    await page.getByRole("button", { name: /^Run$/ }).click();
    await expect(page.getByTestId("agent-run-result")).toContainText("completed");
    await expect(page.getByTestId("agent-run-result")).toContainText("Generated compliance summary");
  });

  test("active agent rollback button is enabled and returns the agent to shadow", async ({ page, baseURL }) => {
    const agents = buildAgents(25);
    await installRoutes(page, agents);

    await page.goto(`${baseURL}/dashboard/agents/agent-1`, { waitUntil: "domcontentloaded" });
    const rollback = page.getByRole("button", { name: "Rollback" });
    await expect(rollback).toBeEnabled();
    await rollback.click();
    await expect(page.locator("main")).toContainText("shadow");
  });
});
