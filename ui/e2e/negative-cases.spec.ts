/**
 * Negative / Error-path E2E Tests
 *
 * Tests error handling, validation, 404s, rate limiting, and edge cases.
 * Runs against PRODUCTION with real browser interactions.
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
// AUTH — Login Error Handling
// ═══════════════════════════════════════════════════════════════════════════

test.describe("AUTH: Login errors", () => {
  test("Wrong password shows error message", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.waitForLoadState("networkidle");
    await page.fill('input[placeholder="you@company.com"]', "ceo@agenticorg.local");
    await page.fill('input[type="password"]', "TotallyWrongPassword1");
    await page.click('button[type="submit"]');
    await page.waitForTimeout(3000);

    // Should show error, NOT navigate to dashboard
    expect(page.url()).toContain("/login");
    const bodyText = await page.textContent("body");
    const hasError = bodyText?.includes("Invalid") || bodyText?.includes("failed") || bodyText?.includes("error");
    expect(hasError).toBeTruthy();
  });

  test("Empty form submission is prevented", async ({ page }) => {
    await page.goto(`${BASE}/login`);
    await page.waitForLoadState("networkidle");

    // Try submitting empty form — HTML validation should prevent it
    await page.click('button[type="submit"]');
    await page.waitForTimeout(1000);

    // Should still be on login page
    expect(page.url()).toContain("/login");
  });
});


// ═══════════════════════════════════════════════════════════════════════════
// AUTH — Signup Validation
// ═══════════════════════════════════════════════════════════════════════════

test.describe("AUTH: Signup validation", () => {
  test("Weak password shows error", async ({ page }) => {
    const ts = Date.now();
    await page.goto(`${BASE}/signup`);
    await page.waitForLoadState("networkidle");

    await page.fill('#orgName', `Test Org ${ts}`);
    await page.fill('#name', "Test User");
    await page.fill('#signupEmail', `weak-${ts}@test.test`);
    await page.fill('#signupPassword', "weak");
    await page.fill('#confirmPassword', "weak");
    await page.click('button[type="submit"]');
    await page.waitForTimeout(3000);

    // Should show password policy error, NOT navigate to onboarding
    expect(page.url()).toContain("/signup");
    const bodyText = await page.textContent("body");
    const hasError = bodyText?.includes("8 characters") || bodyText?.includes("uppercase") || bodyText?.includes("password") || bodyText?.includes("Password");
    expect(hasError).toBeTruthy();
  });
});


// ═══════════════════════════════════════════════════════════════════════════
// API — 404 Handling
// ═══════════════════════════════════════════════════════════════════════════

test.describe("API: 404 responses", () => {
  test("Non-existent agent returns 404", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.get(`${BASE}/api/v1/agents/00000000-0000-0000-0000-000000000000`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(resp.status()).toBe(404);
  });

  test("Non-existent workflow returns 404", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.get(`${BASE}/api/v1/workflows/00000000-0000-0000-0000-000000000000`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(resp.status()).toBe(404);
  });

  test("Non-existent connector returns 404", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.get(`${BASE}/api/v1/connectors/00000000-0000-0000-0000-000000000000`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(resp.status()).toBe(404);
  });

  test("Non-existent HITL item returns 404 on decide", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.post(`${BASE}/api/v1/approvals/00000000-0000-0000-0000-000000000000/decide`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { decision: "approve", notes: "" },
    });
    expect(resp.status()).toBe(404);
  });

  test("Agent detail page shows 'not found' with back link for bad ID", async ({ page }) => {
    await loginAsCeo(page);
    await page.goto(`${BASE}/dashboard/agents/00000000-0000-0000-0000-000000000000`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("not found");

    // Should have a back link
    const backLink = page.locator('a[href="/dashboard/agents"]');
    await expect(backLink).toBeVisible();
  });
});


// ═══════════════════════════════════════════════════════════════════════════
// API — 401 Unauthorized
// ═══════════════════════════════════════════════════════════════════════════

test.describe("API: 401 unauthorized", () => {
  test("Agents endpoint rejects requests without token", async ({ page }) => {
    const resp = await page.request.get(`${BASE}/api/v1/agents`);
    expect(resp.status()).toBe(401);
  });

  test("Workflows endpoint rejects requests without token", async ({ page }) => {
    const resp = await page.request.get(`${BASE}/api/v1/workflows`);
    expect(resp.status()).toBe(401);
  });

  test("Approvals endpoint rejects requests without token", async ({ page }) => {
    const resp = await page.request.get(`${BASE}/api/v1/approvals`);
    expect(resp.status()).toBe(401);
  });

  test("Invalid token returns 401", async ({ page }) => {
    const resp = await page.request.get(`${BASE}/api/v1/agents`, {
      headers: { Authorization: "Bearer invalid.jwt.token" },
    });
    expect(resp.status()).toBe(401);
  });
});


// ═══════════════════════════════════════════════════════════════════════════
// API — Input Validation (400/409/422)
// ═══════════════════════════════════════════════════════════════════════════

test.describe("API: Input validation", () => {
  test("Workflow with empty steps returns 400", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.post(`${BASE}/api/v1/workflows`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { name: "Bad WF", definition: { steps: [] } },
    });
    expect(resp.status()).toBe(400);
    const body = await resp.json();
    expect(body.detail).toContain("at least one step");
  });

  test("Workflow with no definition returns 400", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.post(`${BASE}/api/v1/workflows`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { name: "Bad WF", definition: {} },
    });
    expect(resp.status()).toBe(400);
  });

  test("Duplicate connector name returns 409", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);
    const ts = Date.now();
    const name = `dup-conn-${ts}`;

    // Create first
    await page.request.post(`${BASE}/api/v1/connectors`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { name, category: "comms", auth_type: "none" },
    });

    // Create duplicate
    const resp = await page.request.post(`${BASE}/api/v1/connectors`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { name, category: "comms", auth_type: "none" },
    });
    expect(resp.status()).toBe(409);
  });

  test("Invalid audit date_from returns 400", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.get(`${BASE}/api/v1/audit?date_from=not-a-date`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(resp.status()).toBe(400);
  });

  test("Invalid audit agent_id returns 400", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const resp = await page.request.get(`${BASE}/api/v1/audit?agent_id=not-a-uuid`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(resp.status()).toBe(400);
  });
});


// ═══════════════════════════════════════════════════════════════════════════
// UI — Error Display & Graceful Handling
// ═══════════════════════════════════════════════════════════════════════════

test.describe("UI: Error display", () => {
  test("Workflow create shows error detail on failure", async ({ page }) => {
    await loginAsCeo(page);
    await page.goto(`${BASE}/dashboard/workflows/new`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1000);

    // Fill name but leave steps invalid
    await page.fill('input[placeholder*="Invoice"]', "Test WF");
    const stepsArea = page.locator("textarea");
    await stepsArea.fill("not valid json");

    await page.click('button[type="submit"]');
    await page.waitForTimeout(1000);

    // Should show validation error
    const bodyText = await page.textContent("body");
    const hasError = bodyText?.includes("Invalid JSON") || bodyText?.includes("valid JSON") || bodyText?.includes("error");
    expect(hasError).toBeTruthy();
  });

  test("404 page renders for unknown routes", async ({ page }) => {
    await page.goto(`${BASE}/this-route-does-not-exist`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    const bodyText = await page.textContent("body");
    const has404 = bodyText?.includes("Not Found") || bodyText?.includes("404") || bodyText?.includes("not found");
    expect(has404).toBeTruthy();
  });
});
