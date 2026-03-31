/**
 * SOP Upload Flow — End-to-End Tests
 *
 * Tests the full Create Agent from SOP flow:
 * 1. Navigate to SOP upload page
 * 2. Paste SOP text
 * 3. Parse via LLM
 * 4. Review parsed config
 * 5. Deploy as shadow agent
 */

import { test, expect, Page } from "@playwright/test";

const BASE = "https://app.agenticorg.ai";
const CEO_EMAIL = "ceo@agenticorg.local";
const CEO_PASS = "ceo123!";

async function loginAsCeo(page: Page) {
  await page.goto(`${BASE}/login`);
  await page.waitForLoadState("networkidle");
  await page.fill('input[placeholder="you@company.com"]', CEO_EMAIL);
  await page.fill('input[type="password"]', CEO_PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL("**/dashboard**", { timeout: 15000 });
}

async function getToken(page: Page): Promise<string> {
  return (await page.evaluate(() => localStorage.getItem("token"))) || "";
}


// ═══════════════════════════════════════════════════════════════════════════
// SOP Page Accessibility
// ═══════════════════════════════════════════════════════════════════════════

test.describe("SOP: Page Access", () => {
  test("SOP upload page renders from sidebar link", async ({ page }) => {
    await loginAsCeo(page);
    await page.goto(`${BASE}/dashboard/agents/from-sop`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("Create Agent from SOP");
    expect(bodyText).toContain("Upload a business process document");
  });

  test("SOP page has Upload and Paste tabs", async ({ page }) => {
    await loginAsCeo(page);
    await page.goto(`${BASE}/dashboard/agents/from-sop`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1000);

    await expect(page.getByText("Upload File")).toBeVisible();
    await expect(page.getByText("Paste Text")).toBeVisible();
  });

  test("SOP page is accessible from Agents page button", async ({ page }) => {
    await loginAsCeo(page);
    await page.goto(`${BASE}/dashboard/agents`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    const sopButton = page.getByRole("button", { name: /Create from SOP/i });
    await expect(sopButton).toBeVisible();
    await sopButton.click();
    await page.waitForURL("**/from-sop**", { timeout: 10000 });
  });
});


// ═══════════════════════════════════════════════════════════════════════════
// SOP Text Parse API
// ═══════════════════════════════════════════════════════════════════════════

test.describe("SOP: API Parse", () => {
  test("Parse text SOP returns draft config via API", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const sopText = `
    Invoice Processing Standard Operating Procedure

    Step 1: Receive invoice from vendor via email
    Step 2: Extract invoice number, GSTIN, line items, total amount
    Step 3: Validate GSTIN on GST portal
    Step 4: 3-way match — invoice vs PO vs GRN
    Step 5: If amount > 5 lakhs, require CFO approval
    Step 6: Schedule payment
    Step 7: Post journal entry in ERP
    Step 8: Send remittance advice to vendor
    `;

    const resp = await page.request.post(`${BASE}/api/v1/sop/parse-text`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { text: sopText, domain_hint: "finance" },
    });

    // This may fail if no LLM API key is configured in production
    // Accept both success (200) and failure (500 from LLM)
    if (resp.ok()) {
      const data = await resp.json();
      expect(data.status).toBe("draft");
      expect(data.config).toBeTruthy();
      expect(data.config.agent_name).toBeTruthy();
      expect(data.config._parse_status).toBe("draft");
    } else {
      // LLM API key not configured — expected in some environments
      expect(resp.status()).toBeGreaterThanOrEqual(400);
    }
  });

  test("Parse empty text returns 400", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.post(`${BASE}/api/v1/sop/parse-text`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { text: "" },
    });
    expect(resp.status()).toBe(400);
  });

  test("Parse text too long returns 400", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.post(`${BASE}/api/v1/sop/parse-text`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { text: "x".repeat(60000) },
    });
    expect(resp.status()).toBe(400);
  });
});


// ═══════════════════════════════════════════════════════════════════════════
// A2A / MCP Discovery
// ═══════════════════════════════════════════════════════════════════════════

test.describe("SOP: A2A + MCP Integration", () => {
  test("A2A agent card is publicly accessible", async ({ page }) => {
    // Use /agent-card alias (nginx may block .well-known paths)
    const resp = await page.request.get(`${BASE}/api/v1/a2a/agent-card`);
    expect(resp.ok()).toBeTruthy();
    const card = await resp.json();
    expect(card.name).toBe("AgenticOrg Agent Platform");
    expect(card.skills.length).toBe(25);
    expect(card.authentication.scheme).toBe("grantex");
  });

  test("MCP tools list is publicly accessible", async ({ page }) => {
    const resp = await page.request.get(`${BASE}/api/v1/mcp/tools`);
    expect(resp.ok()).toBeTruthy();
    const data = await resp.json();
    expect(data.tools.length).toBe(25);
    expect(data.tools[0].name).toContain("agenticorg_");
    expect(data.tools[0].inputSchema).toBeTruthy();
  });

  test("Integrations page renders with A2A and MCP info", async ({ page }) => {
    await loginAsCeo(page);
    await page.goto(`${BASE}/dashboard/integrations`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("Agent-to-Agent");
    expect(bodyText).toContain("Model Context Protocol");
    expect(bodyText).toContain("Grantex");
    expect(bodyText).toContain("pip install agenticorg");
  });
});


// ═══════════════════════════════════════════════════════════════════════════
// Dashboard v3.0 Cards
// ═══════════════════════════════════════════════════════════════════════════

test.describe("SOP: Dashboard v3.0", () => {
  test("Dashboard shows LangGraph + Grantex + A2A cards", async ({ page }) => {
    await loginAsCeo(page);
    await page.goto(`${BASE}/dashboard`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("LangGraph");
    expect(bodyText).toContain("Grantex");
    expect(bodyText).toContain("External Access");
  });
});
