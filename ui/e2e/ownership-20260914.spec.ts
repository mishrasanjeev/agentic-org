/**
 * Bug sheet 2026-09-14 rows 17-19/22/52 — per-user ownership, browser replay.
 *
 *  1. A CFO creates a personal agent (API), opens its detail page and sees
 *     the "Personal · owned by you" badge plus its management controls.
 *  2. A CHRO asking for that agent id gets 404 (personal agents are hidden
 *     from other users, not just read-only).
 *  3. A tenant admin sees the agent in the fleet list with a Personal badge.
 *  4. A CFO can open /dashboard/connectors (previously admin-only).
 *
 * The agent is deleted at the end through the API as its owner.
 */
import { expect, test, type Page } from "@playwright/test";
import { DEMO_ROLE_CREDENTIALS, DEMO_USER_CREDENTIALS, setSessionToken } from "./helpers/auth";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const API = process.env.API_URL || APP;

type Creds = { email: string; password: string };

async function tokenFor(page: Page, creds: Creds): Promise<string> {
  const resp = await page.request.post(`${API}/api/v1/auth/login`, {
    data: creds,
    failOnStatusCode: false,
  });
  expect(resp.status(), `login ${creds.email}`).toBe(200);
  const body = await resp.json();
  return body.access_token || "";
}

async function signIn(page: Page, token: string): Promise<void> {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await setSessionToken(page, token);
}

function bearer(token: string) {
  return { Authorization: `Bearer ${token}` };
}

test.describe.serial("Bug sheet 2026-09-14 — personal agent ownership", () => {
  const agentName = `Ownership E2E ${Date.now()}`;
  let agentId = "";
  let companyId = "";
  let cfoToken = "";

  test.afterAll(async ({ browser }) => {
    if (!agentId || !cfoToken) return;
    const context = await browser.newContext();
    const resp = await context.request.delete(`${API}/api/v1/agents/${agentId}`, {
      headers: bearer(cfoToken),
      failOnStatusCode: false,
    });
    expect([200, 204, 404], `cleanup delete of ${agentId}`).toContain(resp.status());
    await context.close();
  });

  test("1. CFO creates a personal agent and sees the owner badge and manage controls", async ({ page }) => {
    cfoToken = await tokenFor(page, DEMO_ROLE_CREDENTIALS.cfo);

    // The fleet list is company-scoped; create the agent in the first
    // company the CFO can see so the admin list in step 3 includes it.
    const companies = await page.request.get(`${API}/api/v1/companies?page=1&per_page=1`, {
      headers: bearer(cfoToken),
      failOnStatusCode: false,
    });
    if (companies.ok()) {
      const data = await companies.json();
      const items = Array.isArray(data) ? data : data?.items ?? [];
      companyId = items[0]?.id ? String(items[0].id) : "";
    }

    const created = await page.request.post(`${API}/api/v1/agents`, {
      headers: bearer(cfoToken),
      data: {
        name: agentName,
        employee_name: agentName,
        agent_type: "ap_processor",
        domain: "finance",
        company_id: companyId || undefined,
        system_prompt_text: "You reconcile ledgers for the ownership E2E replay.",
        // Paused, not shadow: the tenant shadow-agent budget (fleet limits)
        // is shared, and a full budget would make this spec flaky.
        initial_status: "paused",
      },
      failOnStatusCode: false,
    });
    expect(created.status(), await created.text()).toBe(201);
    const body = await created.json();
    agentId = String(body.agent_id || body.id || "");
    expect(agentId, "create response carries the agent id").toBeTruthy();

    const fetched = await page.request.get(`${API}/api/v1/agents/${agentId}`, { headers: bearer(cfoToken) });
    expect(fetched.status()).toBe(200);
    const agent = await fetched.json();
    expect(agent.visibility).toBe("personal");
    expect(agent.owner_user_id).toBeTruthy();

    await signIn(page, cfoToken);
    await page.goto(`${APP}/dashboard/agents/${agentId}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("agent-visibility-badge")).toHaveText("Personal · owned by you", { timeout: 20000 });
    await expect(page.getByRole("button", { name: "Resume" })).toBeVisible();
    await expect(page.getByTestId("agent-readonly-note")).toHaveCount(0);
    await expect(page.getByTestId("agent-visibility-toggle")).toHaveCount(0);
  });

  test("2. CHRO cannot read the CFO's personal agent (404)", async ({ page }) => {
    expect(agentId, "step 1 created the agent").toBeTruthy();
    const chroToken = await tokenFor(page, DEMO_ROLE_CREDENTIALS.chro);
    const resp = await page.request.get(`${API}/api/v1/agents/${agentId}`, {
      headers: bearer(chroToken),
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(404);
  });

  test("3. Admin sees the agent in the fleet list with the Personal badge", async ({ page }) => {
    expect(agentId, "step 1 created the agent").toBeTruthy();
    const adminToken = await tokenFor(page, DEMO_USER_CREDENTIALS);
    if (companyId) {
      await page.addInitScript((id) => window.localStorage.setItem("company_id", id), companyId);
    }
    await signIn(page, adminToken);
    await page.goto(`${APP}/dashboard/agents`, { waitUntil: "domcontentloaded" });
    await page.getByPlaceholder("Search agents...").fill(agentName);
    const card = page.locator("main").locator("div.relative").filter({ hasText: agentName }).first();
    await expect(card).toBeVisible({ timeout: 20000 });
    await expect(card.getByTestId("agent-personal-badge")).toHaveText("Personal");
  });

  test("4. CFO can open the connectors page", async ({ page }) => {
    const token = cfoToken || (await tokenFor(page, DEMO_ROLE_CREDENTIALS.cfo));
    await signIn(page, token);
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "Connectors", exact: true })).toBeVisible({ timeout: 20000 });
    await expect(page).not.toHaveURL(/access-denied/);
    await expect(page.getByTestId("access-denied")).toHaveCount(0);
  });
});
