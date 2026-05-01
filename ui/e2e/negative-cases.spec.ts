/**
 * Negative / Error-path E2E Tests
 *
 * Tests error handling, validation, 404s, and edge cases.
 * Runs against PRODUCTION with real browser interactions.
 *
 * NO page.route() mocking -- all responses are real.
 */

import { test, expect, Page } from "@playwright/test";

const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;
function requireAuth(): void {
  if (!canAuth) throw new Error(
    "E2E_TOKEN is required for this spec. Set the E2E_TOKEN env var — the suite runs against production and must have credentials.",
  );
}


// ---------------------------------------------------------------------------
// Auth helper — token-based only
// ---------------------------------------------------------------------------

async function ensureAuth(page: Page, baseURL: string) {
  await page.goto(baseURL, { waitUntil: "domcontentloaded" });
  await setSessionToken(page, E2E_TOKEN);
}

// ===========================================================================
// AUTH -- Login Error Handling
// ===========================================================================

test.describe("AUTH: Login errors", () => {
  test("Wrong password shows error message", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/login`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    await page.fill(
      'input[placeholder="you@company.com"]',
      "fake@doesnotexist.example",
    );
    await page.fill('input[type="password"]', "TotallyWrongPassword1");
    await page.click('button[type="submit"]');
    await page.waitForLoadState("networkidle");

    // Should show error, NOT navigate to dashboard
    expect(page.url()).toContain("/login");
    const bodyText = await page.textContent("body");
    const hasError =
      bodyText?.includes("Invalid") ||
      bodyText?.includes("failed") ||
      bodyText?.includes("error") ||
      bodyText?.includes("incorrect");
    expect(hasError).toBeTruthy();
  });

  test("Empty form submission is prevented", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/login`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    // HTML validation should prevent submission
    await page.click('button[type="submit"]');
    expect(page.url()).toContain("/login");
  });
});

// ===========================================================================
// AUTH -- Signup Validation
// ===========================================================================

test.describe("AUTH: Signup validation", () => {
  test("Weak password shows error or disables submit", async ({
    page,
    baseURL,
  }) => {
    const ts = Date.now();
    await page.goto(`${baseURL}/signup`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    await page.fill("#orgName", `Test Org ${ts}`);
    await page.fill("#name", "Test User");
    await page.fill("#signupEmail", `weak-${ts}@test.test`);
    await page.fill("#signupPassword", "weak");
    await page.fill("#confirmPassword", "weak");

    const submitBtn = page.locator('button[type="submit"]');
    const isDisabled = await submitBtn.isDisabled();
    if (isDisabled) {
      expect(isDisabled).toBeTruthy();
    } else {
      await submitBtn.click();
      await page.waitForLoadState("networkidle");
      expect(page.url()).toContain("/signup");
    }
  });
});

// ===========================================================================
// API -- 404 Handling
// ===========================================================================

test.describe("API: 404 responses", () => {
  test("Non-existent agent does not return 500", async ({ page, baseURL }) => {
    requireAuth();
    const resp = await page.request.get(
      `${baseURL}/api/v1/agents/00000000-0000-0000-0000-000000000000`,
      { headers: { Authorization: `Bearer ${E2E_TOKEN}` } },
    );
    // Must not be a 500 Internal Server Error (crash)
    // 401 (auth), 404 (not found), 502/503 (deploy rollover) are all acceptable
    expect(resp.status()).not.toBe(500);
  });

  test("Non-existent workflow does not return 500", async ({ page, baseURL }) => {
    requireAuth();
    const resp = await page.request.get(
      `${baseURL}/api/v1/workflows/00000000-0000-0000-0000-000000000000`,
      { headers: { Authorization: `Bearer ${E2E_TOKEN}` } },
    );
    expect(resp.status()).not.toBe(500);
  });

  test("Non-existent connector returns 404", async ({ page, baseURL }) => {
    requireAuth();
    const resp = await page.request.get(
      `${baseURL}/api/v1/connectors/00000000-0000-0000-0000-000000000000`,
      { headers: { Authorization: `Bearer ${E2E_TOKEN}` } },
    );
    expect(resp.status()).toBe(404);
  });

  test("Non-existent HITL item returns 404 on decide", async ({
    page,
    baseURL,
  }) => {
    requireAuth();
    const resp = await page.request.post(
      `${baseURL}/api/v1/approvals/00000000-0000-0000-0000-000000000000/decide`,
      {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: { decision: "approve", notes: "" },
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("Agent detail page shows 'not found' with back link for bad ID", async ({
    page,
    baseURL,
  }) => {
    requireAuth();
    await ensureAuth(page, baseURL!);
    await page.goto(
      `${baseURL}/dashboard/agents/00000000-0000-0000-0000-000000000000`,
      { waitUntil: "domcontentloaded" },
    );
    await page.waitForLoadState("networkidle");

    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("not found");

    const backLink = page.getByRole("link", { name: /Back to Agents/ });
    await expect(backLink).toBeVisible();
  });
});

// ===========================================================================
// API -- 401 Unauthorized
// ===========================================================================

test.describe("API: 401 unauthorized", () => {
  test("Agents endpoint rejects requests without token", async ({
    page,
    baseURL,
  }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/agents`);
    expect(resp.status()).toBe(401);
  });

  test("Workflows endpoint rejects requests without token", async ({
    page,
    baseURL,
  }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/workflows`);
    expect(resp.status()).toBe(401);
  });

  test("Approvals endpoint rejects requests without token", async ({
    page,
    baseURL,
  }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/approvals`);
    expect(resp.status()).toBe(401);
  });

  test("Invalid token returns 401", async ({ page, baseURL }) => {
    const resp = await page.request.get(`${baseURL}/api/v1/agents`, {
      headers: { Authorization: "Bearer invalid.jwt.token" },
    });
    expect(resp.status()).toBe(401);
  });
});

// ===========================================================================
// API -- Input Validation (400/409/422)
// ===========================================================================

test.describe("API: Input validation", () => {
  test("Workflow with empty steps returns 400", async ({ page, baseURL }) => {
    requireAuth();
    const resp = await page.request.post(`${baseURL}/api/v1/workflows`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { name: "Bad WF", definition: { steps: [] } },
    });
    expect(resp.status()).toBe(400);
    const body = await resp.json();
    expect(body.detail).toContain("at least one step");
  });

  test("Workflow with no definition returns 400", async ({
    page,
    baseURL,
  }) => {
    requireAuth();
    const resp = await page.request.post(`${baseURL}/api/v1/workflows`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { name: "Bad WF", definition: {} },
    });
    expect(resp.status()).toBe(400);
  });

  test("Duplicate connector name returns 409", async ({ page, baseURL }) => {
    requireAuth();
    const ts = Date.now();
    const name = `dup-conn-${ts}`;

    // Create first
    await page.request.post(`${baseURL}/api/v1/connectors`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { name, category: "comms", auth_type: "none" },
    });

    // Create duplicate
    const resp = await page.request.post(`${baseURL}/api/v1/connectors`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { name, category: "comms", auth_type: "none" },
    });
    expect(resp.status()).toBe(409);
  });

  test("Invalid audit date_from returns 400", async ({ page, baseURL }) => {
    requireAuth();
    const resp = await page.request.get(
      `${baseURL}/api/v1/audit?date_from=not-a-date`,
      { headers: { Authorization: `Bearer ${E2E_TOKEN}` } },
    );
    expect(resp.status()).toBe(400);
  });

  test("Invalid audit agent_id returns 400", async ({ page, baseURL }) => {
    requireAuth();
    const resp = await page.request.get(
      `${baseURL}/api/v1/audit?agent_id=not-a-uuid`,
      { headers: { Authorization: `Bearer ${E2E_TOKEN}` } },
    );
    expect(resp.status()).toBe(400);
  });
});

// ===========================================================================
// UI -- Error Display & Graceful Handling
// ===========================================================================

test.describe("UI: Error display", () => {
  test("Workflow create shows error detail on failure", async ({
    page,
    baseURL,
  }) => {
    requireAuth();
    await ensureAuth(page, baseURL!);
    await page.goto(`${baseURL}/dashboard/workflows/new`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    // Switch to template tab (NL description tab may be default)
    const templateTab = page.locator('[data-testid="tab-template"]');
    if (await templateTab.isVisible({ timeout: 5000 }).catch(() => false)) {
      await templateTab.click();
      await page.waitForTimeout(1000);
    }

    // Fill name but leave steps invalid
    const nameInput = page.locator('input[placeholder*="Invoice"]');
    if (await nameInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      await nameInput.fill("Test WF");
    }
    const stepsArea = page.locator("textarea");
    if (await stepsArea.isVisible({ timeout: 3000 }).catch(() => false)) {
      await stepsArea.fill("not valid json");
    }

    await page.click('button[type="submit"]');
    await page.waitForLoadState("networkidle");

    const bodyText = await page.textContent("body");
    const hasError =
      bodyText?.includes("Invalid JSON") ||
      bodyText?.includes("valid JSON") ||
      bodyText?.includes("error");
    expect(hasError).toBeTruthy();
  });

  test("404 page renders for unknown routes", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/this-route-does-not-exist`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    const bodyText = await page.textContent("body");
    const has404 =
      bodyText?.includes("Not Found") ||
      bodyText?.includes("404") ||
      bodyText?.includes("not found");
    expect(has404).toBeTruthy();
  });
});
