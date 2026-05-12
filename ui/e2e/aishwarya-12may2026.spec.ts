import { expect, Page, test } from "@playwright/test";

const tdsAgent = {
  id: "agent-tds",
  name: "TDS Compliance Agent",
  employee_name: "TDS Compliance Agent",
  agent_type: "tds_compliance_agent",
  domain: "finance",
  status: "active",
  version: "1.0.1",
  confidence_floor: 0.92,
  shadow_sample_count: 22,
  shadow_accuracy_current: 0.87,
  shadow_accuracy_floor: 0.8,
  shadow_min_samples: 10,
  authorized_tools: ["calculate_tds", "pay_tax_challan"],
  created_at: "2026-05-12T00:00:00Z",
};

async function installRoutes(page: Page) {
  let detailAgent = { ...tdsAgent };

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
        json: { version: "test", connector_count: 10, agent_count: 1, tool_count: 20 },
      });
      return;
    }

    if (path.endsWith("/agents/agent-tds") && request.method() === "GET") {
      await route.fulfill({ json: detailAgent });
      return;
    }

    if (path.endsWith("/agents/agent-tds/run") && request.method() === "POST") {
      await route.fulfill({
        json: {
          run_id: "run-tool-failure",
          task_id: "run-tool-failure",
          agent_id: "agent-tds",
          status: "completed",
          confidence: 0.5,
          output: { answer: "Ledger fetch failed; retry after connector access is restored." },
          reasoning_trace: ["tool infrastructure failure handled without hallucination"],
          tool_calls: [{ tool: "get_ledger_balance", status: "error", result: "connector timeout" }],
          performance: { total_latency_ms: 10, llm_tokens_used: 0, llm_cost_usd: 0 },
          hitl_trigger: null,
          error: null,
        },
      });
      return;
    }

    if (path.endsWith("/agents/agent-tds/rollback") && request.method() === "POST") {
      detailAgent = { ...detailAgent, status: "shadow", version: "1.0.0" };
      await route.fulfill({
        json: {
          id: "agent-tds",
          rolled_back: true,
          from_status: "active",
          to_status: "shadow",
          from_version: "1.0.1",
          to_version: "1.0.0",
        },
      });
      return;
    }

    if (path.endsWith("/chat/history")) {
      await route.fulfill({ json: [] });
      return;
    }

    if (path.endsWith("/chat/query") && request.method() === "POST") {
      const body = request.postDataJSON() as { query?: string };
      const query = body.query || "";
      if (query.includes("12,50,000")) {
        await route.fulfill({
          json: {
            answer:
              "Calculated TDS amount: INR 12,500. HITL triggered: gross transaction amount INR 12,50,000 exceeds INR 5,00,000. No filing or payment was auto-submitted.",
            agent: "TDS Compliance Agent",
            confidence: 0.9,
            domain: "finance",
            hitl_trigger: "transaction_amount 1250000.00 > 500000.00",
          },
        });
        return;
      }
      if (query.includes("Challan 281")) {
        await route.fulfill({
          json: {
            answer:
              "Challan 281 preparation started. TDS amount to pay: INR 125,000.00. Missing: section, TAN, PAN, partner-review approval. No payment call was made.",
            agent: "TDS Compliance Agent",
            confidence: 0.82,
            domain: "finance",
            hitl_trigger: "challan_281_payment_requires_partner_review",
          },
        });
        return;
      }
      await route.fulfill({
        json: {
          answer:
            "Section 234E / 201(1A) computation route selected for Form 26Q. Period extracted: Q4 FY 2025-26. Statutory filing due date: 2026-05-31. Missing: TDS amount payable and actual delay.",
          agent: "TDS Compliance Agent",
          confidence: 0.8,
          domain: "finance",
          hitl_trigger: "tds_late_fee_or_interest_requires_partner_review",
        },
      });
      return;
    }

    if (path.endsWith("/agents/agent-tds/feedback")) {
      await route.fulfill({ json: { feedback: [], count: 0 } });
      return;
    }

    if (path.endsWith("/agents/agent-tds/explanation/latest")) {
      await route.fulfill({ json: { has_run: false, bullets: [], tools_cited: [] } });
      return;
    }

    await route.fulfill({ json: {} });
  });
}

test.describe("Aishwarya 12 May 2026 TDS regressions", () => {
  test("tool failure run keeps shadow metrics visible and rollback returns active to shadow", async ({
    page,
    baseURL,
  }) => {
    await installRoutes(page);

    await page.goto(`${baseURL}/dashboard/agents/agent-tds`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("main")).toContainText("87.0%");

    await page.getByRole("button", { name: "Run Agent" }).click();
    await page
      .getByPlaceholder("What should the agent do?")
      .fill("Calculate TDS for contractor payments and fetch ledger balances.");
    await page.getByRole("button", { name: /^Run$/ }).click();
    await expect(page.getByTestId("agent-run-result")).toContainText("Ledger fetch failed");
    await expect(page.locator("main")).toContainText("87.0%");

    await page.getByRole("button", { name: "Rollback" }).click();
    await expect(page.locator("main")).toContainText("shadow");
  });

  test("TDS chat surfaces HITL, Challan 281 extraction, and late-fee logic", async ({
    page,
    baseURL,
  }) => {
    await installRoutes(page);

    await page.goto(`${baseURL}/dashboard/agents/agent-tds`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Chat with Agent" }).click();

    const input = page.getByPlaceholder("Type a message...").last();
    await input.fill(
      "Calculate TDS for contractor payment of INR 12,50,000 under Section 194C for Q2 FY 2026-27.",
    );
    await input.press("Enter");
    await expect(page.locator("body")).toContainText("gross transaction amount");
    await expect(page.locator("body")).toContainText("HITL");

    await input.fill("Generate Challan 281 for April 2026 TDS payment of INR 1,25,000.");
    await input.press("Enter");
    await expect(page.locator("body")).toContainText("TDS amount to pay: INR 125,000.00");
    await expect(page.locator("body")).not.toContainText("Amount of TDS to be paid");

    await input.fill("Compute late filing interest and penalty for delayed Form 26Q filing for Q4 FY 2025-26.");
    await input.press("Enter");
    await expect(page.locator("body")).toContainText("Section 234E / 201(1A)");
    await expect(page.locator("body")).toContainText("2026-05-31");
  });
});
