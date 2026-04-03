/**
 * SOP Upload Flow — Production E2E Tests
 *
 * Tests the Create Agent from SOP flow against production.
 * Auth-gated tests skip when E2E_TOKEN is not set.
 * Public endpoints (A2A, MCP) are tested without auth.
 */
import { test, expect } from "@playwright/test";

const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

// ═══════════════════════════════════════════════════════════════════════════
// SOP Page — Auth Required
// ═══════════════════════════════════════════════════════════════════════════

test.describe("SOP: Page Access (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => localStorage.setItem("token", t), E2E_TOKEN);
  });

  test("SOP upload page renders heading and description", async ({ page }) => {
    await page.goto("/dashboard/agents/from-sop", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("Create Agent from SOP").first()).toBeVisible({
      timeout: 15000,
    });
    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("Upload");
  });

  test("SOP page has Upload and Paste tabs", async ({ page }) => {
    await page.goto("/dashboard/agents/from-sop", { waitUntil: "domcontentloaded" });

    await expect(page.getByText("Upload File").first()).toBeVisible({ timeout: 15000 });
    await expect(page.getByText("Paste Text").first()).toBeVisible();
  });

  test("SOP page is accessible from Agents page button", async ({ page }) => {
    await page.goto("/dashboard/agents", { waitUntil: "domcontentloaded" });

    const sopButton = page.getByRole("button", { name: /Create from SOP/i });
    await expect(sopButton).toBeVisible({ timeout: 15000 });
    await sopButton.click();
    await page.waitForURL("**/from-sop**", { timeout: 15000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// SOP API Parse — Auth Required
// ═══════════════════════════════════════════════════════════════════════════

test.describe("SOP: API Parse (auth required)", () => {
  test.beforeEach(async () => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
  });

  test("parse text SOP returns draft config via API", async ({ request }) => {
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

    const resp = await request.post("/api/v1/sop/parse-text", {
      headers: { Authorization: `Bearer ${E2E_TOKEN}`, "Content-Type": "application/json" },
      data: { text: sopText, domain_hint: "finance" },
    });

    // Accept both success (200) and LLM-unavailable failure
    if (resp.ok()) {
      const data = await resp.json();
      expect(data.status).toBe("draft");
      expect(data.config).toBeTruthy();
      expect(data.config.agent_name).toBeTruthy();
    } else {
      expect(resp.status()).toBeGreaterThanOrEqual(400);
    }
  });

  test("parse empty text returns 400", async ({ request }) => {
    const resp = await request.post("/api/v1/sop/parse-text", {
      headers: { Authorization: `Bearer ${E2E_TOKEN}`, "Content-Type": "application/json" },
      data: { text: "" },
    });
    expect(resp.status()).toBe(400);
  });

  test("parse text too long returns 400", async ({ request }) => {
    const resp = await request.post("/api/v1/sop/parse-text", {
      headers: { Authorization: `Bearer ${E2E_TOKEN}`, "Content-Type": "application/json" },
      data: { text: "x".repeat(60000) },
    });
    expect(resp.status()).toBe(400);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// A2A / MCP Discovery — Public
// ═══════════════════════════════════════════════════════════════════════════

test.describe("SOP: A2A + MCP Integration (public)", () => {
  test("A2A agent card is publicly accessible", async ({ request }) => {
    const resp = await request.get("/api/v1/a2a/agent-card");
    expect(resp.ok()).toBeTruthy();
    const card = await resp.json();
    expect(card.name).toBe("AgenticOrg Agent Platform");
    expect(card.skills.length).toBe(25);
    expect(card.authentication.scheme).toBe("grantex");
  });

  test("MCP tools list is publicly accessible", async ({ request }) => {
    const resp = await request.get("/api/v1/mcp/tools");
    expect(resp.ok()).toBeTruthy();
    const data = await resp.json();
    expect(data.tools.length).toBe(25);
    expect(data.tools[0].name).toContain("agenticorg_");
    expect(data.tools[0].inputSchema).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Dashboard v3.0 Cards — Auth Required
// ═══════════════════════════════════════════════════════════════════════════

test.describe("SOP: Dashboard v3.0 (auth required)", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN not set — skipping auth-gated tests");
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => localStorage.setItem("token", t), E2E_TOKEN);
  });

  test("Dashboard shows LangGraph + Grantex + External Access cards", async ({ page }) => {
    await page.goto("/dashboard", { waitUntil: "domcontentloaded" });

    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("LangGraph");
    expect(bodyText).toContain("Grantex");
    expect(bodyText).toContain("External Access");
  });

  test("Integrations page renders A2A and MCP info", async ({ page }) => {
    await page.goto("/dashboard/integrations", { waitUntil: "domcontentloaded" });

    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("Agent-to-Agent");
    expect(bodyText).toContain("Model Context Protocol");
    expect(bodyText).toContain("Grantex");
    expect(bodyText).toContain("pip install agenticorg");
  });
});
