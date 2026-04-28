/**
 * Module 30: Per-Agent Budget Enforcement — TC-BUDGET-007 + 008.
 *
 * The other 6 TCs in this module pin server-side contracts and
 * live in tests/unit/test_module_30_per_agent_budget.py. The two
 * here are the UI-only ones:
 *
 *  - TC-BUDGET-007: Cost tab in Agent Detail UI shows the cap +
 *    current spend + utilization bar.
 *  - TC-BUDGET-008: Cost tab shows "no budget configured" when
 *    monthly_cap_usd is unset.
 *
 * Strategy: API-driven setup — create an agent via POST /agents
 * with the desired cost_controls payload, then drive the UI
 * Cost tab and assert the rendered text. This keeps the spec
 * deterministic without depending on prior fixture data.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

interface AgentResponse {
  id: string;
}

async function createAgent(
  request: import("@playwright/test").APIRequestContext,
  costControls: Record<string, unknown>,
): Promise<string> {
  const ts = Date.now();
  const resp = await request.post(`${APP}/api/v1/agents`, {
    headers: {
      Authorization: `Bearer ${E2E_TOKEN}`,
      "Content-Type": "application/json",
    },
    data: {
      name: `qa-module-30-${ts}`,
      role: "Test Agent",
      goal: "Verify Cost tab rendering",
      tools: [],
      cost_controls: costControls,
    },
    failOnStatusCode: false,
  });
  expect(resp.status(), `agent create failed: ${resp.status()}`).toBeLessThan(300);
  const body = (await resp.json()) as AgentResponse;
  expect(body.id).toBeTruthy();
  return body.id;
}

async function deleteAgent(
  request: import("@playwright/test").APIRequestContext,
  agentId: string,
): Promise<void> {
  // Best-effort cleanup — don't fail the test if the agent is
  // already gone.
  await request.delete(`${APP}/api/v1/agents/${agentId}`, {
    headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    failOnStatusCode: false,
  });
}

test.describe("Module 30: Per-Agent Budget Enforcement @qa @budget", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-BUDGET-007: Cost tab shows cap + current spend + utilization
  // -------------------------------------------------------------------------

  test("TC-BUDGET-007 Cost tab in Agent Detail shows monthly cap + utilization bar", async ({
    page,
    request,
  }) => {
    const agentId = await createAgent(request, {
      monthly_cap_usd: 50,
      cost_current_usd: 12.5, // 25% utilization → green bar
    });
    try {
      await page.goto(`${APP}/agents/${agentId}`);

      // Switch to the Cost tab.
      await page.getByRole("tab", { name: /cost/i }).click();

      // Cap rendered as $50.00, current as $12.50.
      await expect(
        page.getByText("$50.00", { exact: true }).first(),
      ).toBeVisible();
      await expect(
        page.getByText("$12.50", { exact: true }).first(),
      ).toBeVisible();

      // Utilization label includes a percent. We assert the column
      // header + the % suffix without pinning the exact number so
      // the test is robust against rounding.
      await expect(page.getByText(/Budget Utilization/i)).toBeVisible();
      await expect(page.getByText(/25\.0%|25%/)).toBeVisible();

      // The green/yellow/red state is implied by the bar color
      // class; rather than assert internal CSS, we assert the
      // human-readable status copy.
      await expect(page.getByText(/Within budget/i)).toBeVisible();
    } finally {
      await deleteAgent(request, agentId);
    }
  });

  test("TC-BUDGET-007b Cost tab flips to 'Approaching budget limit' near 80%", async ({
    page,
    request,
  }) => {
    const agentId = await createAgent(request, {
      monthly_cap_usd: 100,
      cost_current_usd: 85, // 85% → yellow / amber state
    });
    try {
      await page.goto(`${APP}/agents/${agentId}`);
      await page.getByRole("tab", { name: /cost/i }).click();
      await expect(page.getByText(/Approaching budget limit/i)).toBeVisible();
    } finally {
      await deleteAgent(request, agentId);
    }
  });

  test("TC-BUDGET-007c Cost tab shows 'Over budget!' when current > cap", async ({
    page,
    request,
  }) => {
    const agentId = await createAgent(request, {
      monthly_cap_usd: 20,
      cost_current_usd: 25, // 125% → over budget
    });
    try {
      await page.goto(`${APP}/agents/${agentId}`);
      await page.getByRole("tab", { name: /cost/i }).click();
      await expect(page.getByText(/Over budget/i)).toBeVisible();
    } finally {
      await deleteAgent(request, agentId);
    }
  });

  // -------------------------------------------------------------------------
  // TC-BUDGET-008: Cost tab shows "no budget configured" when cap is unset
  // -------------------------------------------------------------------------

  test("TC-BUDGET-008 Cost tab shows 'No monthly cost cap configured' when cap is 0", async ({
    page,
    request,
  }) => {
    const agentId = await createAgent(request, {
      // No monthly_cap_usd → defaults to 0 in the schema.
      cost_current_usd: 0,
    });
    try {
      await page.goto(`${APP}/agents/${agentId}`);
      await page.getByRole("tab", { name: /cost/i }).click();

      // The empty-state copy is exact-text — pinning the wording
      // guards against silent UX regressions where the empty
      // state shows nothing or shows a misleading "$0 cap" instead.
      await expect(
        page.getByText(/No monthly cost cap configured for this agent/i),
      ).toBeVisible();

      // The cap column shows the empty marker, not a $0 figure.
      await expect(page.getByText(/No cap set/i)).toBeVisible();

      // The utilization bar must NOT render when there's no cap
      // (otherwise users see a 100% green bar and think it's fine).
      await expect(page.getByText(/Budget Utilization/i)).not.toBeVisible();
    } finally {
      await deleteAgent(request, agentId);
    }
  });
});
