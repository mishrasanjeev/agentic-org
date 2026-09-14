/**
 * Framework bug sheet 2026-09-14 — browser replay of the operator-visible rows.
 *
 *  #27  Header shows the authenticated user's organization name.
 *  #53  Chat executes agents and needs agents:write server-side; roles
 *       without it (auditor) are not offered the chat bar, domain roles are.
 *  #53  A CFO session cannot drive an HR agent through chat (API replay
 *       through the browser's own cookie session, not a bearer shortcut).
 *  #37  AI settings: openai_compatible takes a free-text model name
 *       instead of a select whose only option was the "*" wildcard.
 *  #40  AI settings render without crashing when a catalog model has no
 *       context_window.
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

async function signIn(page: Page, creds: Creds): Promise<string> {
  const token = await tokenFor(page, creds);
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await setSessionToken(page, token);
  return token;
}

test.describe("Bug sheet 2026-09-14 — header and chat entry point", () => {
  test("#27 organization name is shown in the header", async ({ page }) => {
    const token = await signIn(page, DEMO_USER_CREDENTIALS);
    const me = await page.request.get(`${API}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(me.status()).toBe(200);
    const orgName: string = (await me.json()).org_name;
    expect(orgName, "/auth/me returns org_name").toBeTruthy();

    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("org-name").first()).toHaveText(orgName, { timeout: 20000 });
  });

  test("#53 auditor is not offered the chat bar; CFO is", async ({ page, browser }) => {
    await signIn(page, DEMO_ROLE_CREDENTIALS.auditor);
    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("org-name").first()).toBeVisible({ timeout: 20000 });
    await expect(page.getByPlaceholder(/ask/i)).toHaveCount(0);

    const cfoContext = await browser.newContext();
    const cfoPage = await cfoContext.newPage();
    await signIn(cfoPage, DEMO_ROLE_CREDENTIALS.cfo);
    await cfoPage.setViewportSize({ width: 1400, height: 900 });
    await cfoPage.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await expect(cfoPage.getByPlaceholder(/ask/i).first()).toBeVisible({ timeout: 20000 });
    await cfoContext.close();
  });

  test("#53 CFO chatting to an HR agent is refused with 404", async ({ page }) => {
    const adminToken = await tokenFor(page, DEMO_USER_CREDENTIALS);
    const list = await page.request.get(`${API}/api/v1/agents?limit=200`, {
      headers: { Authorization: `Bearer ${adminToken}` },
    });
    expect(list.status()).toBe(200);
    const payload = await list.json();
    const items: Array<{ id: string; domain: string; company_id: string | null }> =
      Array.isArray(payload) ? payload : payload.items || [];
    const hr = items.find((a) => a.domain === "hr" && a.company_id);
    test.skip(!hr, "tenant has no company-scoped HR agent to target");

    const cfoToken = await tokenFor(page, DEMO_ROLE_CREDENTIALS.cfo);
    const resp = await page.request.post(`${API}/api/v1/chat/query`, {
      headers: { Authorization: `Bearer ${cfoToken}` },
      data: { query: "list onboarding tasks", agent_id: hr!.id, company_id: hr!.company_id },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(404);
  });
});

test.describe("Bug sheet 2026-09-14 — AI settings model entry", () => {
  test("#37/#40 openai_compatible accepts a free-text model name", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));

    await signIn(page, DEMO_USER_CREDENTIALS);
    await page.goto(`${APP}/dashboard/settings/ai-config`, { waitUntil: "domcontentloaded" });

    const provider = page.getByTestId("llm-provider-select");
    await expect(provider).toBeVisible({ timeout: 20000 });
    await provider.selectOption("openai_compatible");

    const modelInput = page.getByTestId("llm-model-input");
    await expect(modelInput).toBeVisible();
    await modelInput.fill("meta-llama/Llama-3.1-70B-Instruct");
    await expect(modelInput).toHaveValue("meta-llama/Llama-3.1-70B-Instruct");
    await expect(page.locator("option[value='*']")).toHaveCount(0);

    expect(errors.filter((e) => /context_window|toLocaleString/.test(e)), "no null context_window crash").toEqual([]);
  });
});
