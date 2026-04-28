/**
 * Module 28: Org Chart Hierarchy & Parent Escalation —
 * TC-ORG-CHART-002 + 007 + 008.
 *
 * The escalation logic itself (TC-003 through 006) is async
 * + DB-bound; source-pinned in
 * tests/unit/test_module_28_org_chart.py. The UI tests here
 * cover the schema-shape contracts that gate every chart
 * mutation:
 *
 *  - 002: Agent detail UI shows parent_agent_id / reporting_to.
 *  - 007: PATCH /agents/{id} accepts parent_agent_id.
 *  - 008: PATCH /agents/{id} accepts parent_agent_id=null
 *    to remove an agent from the chart.
 *
 * Strategy: API-only assertions on agent create + PATCH so we
 * don't depend on a fully populated org chart in the test
 * environment.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

interface AgentResponse {
  id: string;
  parent_agent_id: string | null;
  reporting_to?: string | null;
}

async function createAgent(
  request: import("@playwright/test").APIRequestContext,
  body: Record<string, unknown>,
): Promise<string> {
  const ts = Date.now() + Math.floor(Math.random() * 1000);
  const resp = await request.post(`${APP}/api/v1/agents`, {
    headers: {
      Authorization: `Bearer ${E2E_TOKEN}`,
      "Content-Type": "application/json",
    },
    data: {
      name: `qa-module-28-${ts}`,
      role: "Test Agent",
      goal: "Verify org chart contract",
      tools: [],
      ...body,
    },
    failOnStatusCode: false,
  });
  expect(resp.status(), `agent create failed: ${resp.status()}`).toBeLessThan(300);
  const json = (await resp.json()) as AgentResponse;
  expect(json.id).toBeTruthy();
  return json.id;
}

async function deleteAgent(
  request: import("@playwright/test").APIRequestContext,
  agentId: string,
): Promise<void> {
  await request.delete(`${APP}/api/v1/agents/${agentId}`, {
    headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    failOnStatusCode: false,
  });
}

test.describe("Module 28: Org Chart Hierarchy @qa @org-chart @escalation", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-ORG-CHART-002: View hierarchy in agent detail
  // -------------------------------------------------------------------------

  test("TC-ORG-CHART-002 GET /agents/{id} returns parent_agent_id field", async ({
    request,
  }) => {
    // Create a child agent with no parent — the field must still
    // be present (as null) in the response.
    const childId = await createAgent(request, {});
    try {
      const resp = await request.get(`${APP}/api/v1/agents/${childId}`, {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      });
      expect(resp.status()).toBeLessThan(300);
      const body = (await resp.json()) as AgentResponse;
      // The field must be present in the response shape — even
      // when null. Otherwise the UI's hierarchy pane silently
      // shows blank.
      expect(body).toHaveProperty("parent_agent_id");
      expect(body.parent_agent_id).toBeNull();
    } finally {
      await deleteAgent(request, childId);
    }
  });

  test("TC-ORG-CHART-002b POST /agents accepts reporting_to alongside parent_agent_id", async ({
    request,
  }) => {
    // ``reporting_to`` is the human-readable label (e.g. "CFO").
    // ``parent_agent_id`` is the actual UUID FK. Both ship in
    // the create payload.
    const childId = await createAgent(request, {
      reporting_to: "Test Manager",
    });
    try {
      const resp = await request.get(`${APP}/api/v1/agents/${childId}`, {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      });
      expect(resp.status()).toBeLessThan(300);
      const body = (await resp.json()) as AgentResponse;
      expect(body.reporting_to).toBe("Test Manager");
    } finally {
      await deleteAgent(request, childId);
    }
  });

  // -------------------------------------------------------------------------
  // TC-ORG-CHART-007: Update parent_agent_id via PATCH
  // -------------------------------------------------------------------------

  test("TC-ORG-CHART-007 PATCH /agents/{id} can set parent_agent_id", async ({
    request,
  }) => {
    const parentId = await createAgent(request, {});
    const childId = await createAgent(request, {});
    try {
      const resp = await request.patch(`${APP}/api/v1/agents/${childId}`, {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: { parent_agent_id: parentId },
        failOnStatusCode: false,
      });
      expect(resp.status()).toBeLessThan(300);

      // Read back to confirm the link took.
      const verify = await request.get(`${APP}/api/v1/agents/${childId}`, {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      });
      expect(verify.status()).toBeLessThan(300);
      const body = (await verify.json()) as AgentResponse;
      expect(body.parent_agent_id).toBe(parentId);
    } finally {
      await deleteAgent(request, childId);
      await deleteAgent(request, parentId);
    }
  });

  // -------------------------------------------------------------------------
  // TC-ORG-CHART-008: Remove parent (set to null)
  // -------------------------------------------------------------------------

  test("TC-ORG-CHART-008 PATCH /agents/{id} can set parent_agent_id to null", async ({
    request,
  }) => {
    const parentId = await createAgent(request, {});
    const childId = await createAgent(request, { parent_agent_id: parentId });
    try {
      // Set parent_agent_id back to null (remove from chart).
      const resp = await request.patch(`${APP}/api/v1/agents/${childId}`, {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: { parent_agent_id: null },
        failOnStatusCode: false,
      });
      expect(resp.status()).toBeLessThan(300);

      const verify = await request.get(`${APP}/api/v1/agents/${childId}`, {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      });
      expect(verify.status()).toBeLessThan(300);
      const body = (await verify.json()) as AgentResponse;
      // A successful "remove from chart" leaves parent_agent_id
      // null. If the API silently kept the old parent (PATCH
      // with explicit null was treated as "no change"), this
      // assertion catches it.
      expect(body.parent_agent_id).toBeNull();
    } finally {
      await deleteAgent(request, childId);
      await deleteAgent(request, parentId);
    }
  });
});
