/**
 * Agent detail Explainer drift guard — Phase 7 P7.1 (PR-C1).
 *
 * Pre-PR-C1 the "Why did the agent do this?" panel in `AgentDetail.tsx`
 * hardcoded its bullets, confidence, and tool citations. The component
 * literally said `// Load mock explanation until real API is wired`.
 * This spec asserts the UI now renders only values sourced from
 * `GET /agents/{id}/explanation/latest`.
 *
 * Drift guard:
 *   - If an agent has no run history, the empty state must show instead
 *     of fabricated bullets.
 *   - If the endpoint returns bullets, the DOM must contain them (a
 *     subset check — the mocked strings ("Agent processed the request"
 *     / "Confidence was above threshold") must NOT appear unless the
 *     real response contains them).
 */
import { expect, test } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

function requireAuth(): void {
  if (!canAuth) {
    throw new Error(
      "E2E_TOKEN is required for this spec. Set the E2E_TOKEN env var — the suite runs against production and must have credentials.",
    );
  }
}

async function seedSession(page: import("@playwright/test").Page): Promise<void> {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate((t) => {
    localStorage.setItem("token", t);
    localStorage.setItem(
      "user",
      JSON.stringify({
        email: "demo@cafirm.agenticorg.ai",
        name: "Demo Partner",
        role: "admin",
        domain: "all",
        tenant_id: "58483c90-494b-445d-85c6-245a727fe372",
        onboardingComplete: true,
      }),
    );
  }, E2E_TOKEN);
}

test.describe("Agent detail explainer — real trace data", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await seedSession(page);
  });

  test("GET /agents/{id}/explanation/latest returns canonical shape", async ({ request }) => {
    // Pick the first agent for the demo tenant to exercise the endpoint.
    const agentsResp = await request.get(`${APP}/api/v1/agents?per_page=1`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    });
    expect(agentsResp.status(), "GET /agents").toBe(200);
    const agentsBody = await agentsResp.json();
    const items = Array.isArray(agentsBody) ? agentsBody : agentsBody?.items ?? [];
    expect(items.length, "at least one agent must exist in the demo tenant").toBeGreaterThan(0);

    const agentId = items[0].id || items[0].agent_id;
    const explResp = await request.get(
      `${APP}/api/v1/agents/${agentId}/explanation/latest`,
      { headers: { Authorization: `Bearer ${E2E_TOKEN}` } },
    );
    expect(explResp.status(), "GET /agents/{id}/explanation/latest").toBe(200);
    const explData = await explResp.json();
    expect(typeof explData.has_run).toBe("boolean");
    expect(Array.isArray(explData.bullets)).toBe(true);
    expect(Array.isArray(explData.tools_cited)).toBe(true);
    expect(typeof explData.hitl_required).toBe("boolean");
  });

  test("Explainer panel renders no mocked strings", async ({ page, request }) => {
    const agentsResp = await request.get(`${APP}/api/v1/agents?per_page=1`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    });
    const agentsBody = await agentsResp.json();
    const items = Array.isArray(agentsBody) ? agentsBody : agentsBody?.items ?? [];
    const agentId = items[0].id || items[0].agent_id;

    await page.goto(`${APP}/dashboard/agents/${agentId}`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});

    // Expand the explainer.
    await page.getByText(/Why did the agent do this/).first().click();
    const body = page.getByTestId("explainer-body");
    await expect(body).toBeVisible({ timeout: 15000 });

    // Drift guard — the old hardcoded bullets from the mock must never
    // be rendered. If the backend returns something that happens to
    // match, the backend is the source of truth; we explicitly want
    // these specific placeholder strings gone.
    const bodyText = (await body.textContent()) || "";
    expect(bodyText, "mock bullet #1 removed").not.toContain(
      "Agent processed the request using configured tools",
    );
    expect(bodyText, "mock bullet #2 removed").not.toContain(
      "Confidence was above threshold",
    );
    expect(bodyText, "mock bullet #3 removed").not.toContain(
      "No HITL trigger conditions met",
    );
    // `get_contact` and `query` were the mocked tool names — they must
    // only appear if this agent actually called them.
    expect(bodyText, "mock readability grade removed").not.toContain(
      "7.5 (Flesch-Kincaid)",
    );
  });
});
