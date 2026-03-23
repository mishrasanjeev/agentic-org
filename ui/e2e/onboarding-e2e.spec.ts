/**
 * End-to-end tests for signup, onboarding, team invite, and org management flows.
 */
import { test, expect, Page } from "@playwright/test";

const APP = "https://app.agenticorg.ai";
const UNIQUE = Date.now().toString(36); // unique per run to avoid email collisions

// ═══════════════════════════════════════════════════════════
// 1. SIGNUP FLOW
// ═══════════════════════════════════════════════════════════

test.describe("Signup Flow", () => {
  test("Signup page loads and shows form", async ({ page }) => {
    await page.goto(`${APP}/signup`, { waitUntil: "networkidle" });
    await expect(page.getByText("Create your organization")).toBeVisible({ timeout: 10000 });
    await expect(page.locator('input[type="email"]')).toBeVisible();
    await expect(page.locator('input[type="password"]').first()).toBeVisible();
  });

  test("Signup with valid data creates org and redirects to onboarding", async ({ page }) => {
    await page.goto(`${APP}/signup`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);

    // Fill form fields by label
    await page.getByLabel(/organization/i).fill(`Test Org ${UNIQUE}`);
    await page.getByLabel(/your name/i).fill("E2E Test User");
    await page.getByLabel(/email/i).fill(`e2e-${UNIQUE}@test.agenticorg.local`);
    const pwFields = page.locator('input[type="password"]');
    await pwFields.nth(0).fill("TestPass123!");
    if (await pwFields.nth(1).isVisible()) {
      await pwFields.nth(1).fill("TestPass123!");
    }

    // Submit
    await page.getByRole("button", { name: /create account/i }).click();
    // Should redirect to /onboarding or /dashboard
    await page.waitForURL(/\/(onboarding|dashboard)/, { timeout: 15000 });
    expect(page.url()).not.toContain("/signup");
  });

  test("Signup with existing email shows error", async ({ page }) => {
    await page.goto(`${APP}/signup`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);

    const allInputs = page.locator('input:visible');
    const count = await allInputs.count();
    for (let i = 0; i < count; i++) {
      const input = allInputs.nth(i);
      const type = await input.getAttribute("type");
      const placeholder = (await input.getAttribute("placeholder")) || "";
      const name = (await input.getAttribute("name")) || "";

      if (name.includes("org") || placeholder.toLowerCase().includes("org")) {
        await input.fill("Duplicate Test");
      } else if (name.includes("name") || placeholder.toLowerCase().includes("name")) {
        await input.fill("Duplicate User");
      } else if (type === "email") {
        await input.fill("ceo@agenticorg.local"); // existing user
      } else if (type === "password") {
        await input.fill("TestPass123!");
      }
    }

    const submitBtn = page.getByRole("button", { name: /create|sign up|get started/i }).first();
    if (await submitBtn.isVisible()) {
      await submitBtn.click();
      await page.waitForTimeout(3000);
      // Should show error or stay on signup
      const hasError = await page.locator("text=/already|exists|registered|error/i").isVisible({ timeout: 5000 }).catch(() => false);
      const stayedOnSignup = page.url().includes("/signup");
      expect(hasError || stayedOnSignup).toBe(true);
    }
  });

  test("Login page has 'Create new organization' link", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    const createLink = page.getByText(/create.*organization/i).first();
    await expect(createLink).toBeVisible({ timeout: 10000 });
    await createLink.click();
    await page.waitForURL(/\/signup/, { timeout: 5000 });
  });

  test("Login page has demo toggle that reveals test credentials", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await page.waitForTimeout(1000);
    const demoToggle = page.getByText(/demo/i).first();
    await expect(demoToggle).toBeVisible({ timeout: 10000 });
    await demoToggle.click();
    await page.waitForTimeout(500);
    // Demo credentials should now be visible
    await expect(page.getByText(/ceo@agenticorg/i).first()).toBeVisible({ timeout: 5000 });
  });
});

// ═══════════════════════════════════════════════════════════
// 2. ONBOARDING WIZARD
// ═══════════════════════════════════════════════════════════

test.describe("Onboarding Wizard", () => {
  test("Onboarding page loads for new users", async ({ page }) => {
    await page.goto(`${APP}/onboarding`, { waitUntil: "networkidle" });
    // Should redirect to login if not authenticated
    await page.waitForURL(/\/(login|onboarding)/, { timeout: 10000 });
  });
});

// ═══════════════════════════════════════════════════════════
// 3. SIGNUP API
// ═══════════════════════════════════════════════════════════

test.describe("Signup API", () => {
  test("POST /auth/signup creates org and returns token", async ({ request }) => {
    const resp = await request.post(`${APP}/api/v1/auth/signup`, {
      data: {
        org_name: `API Test Org ${UNIQUE}`,
        admin_name: "API Test Admin",
        admin_email: `api-${UNIQUE}@test.agenticorg.local`,
        password: "ApiTest123!",
      },
    });
    // Should be 201 or 200
    expect([200, 201]).toContain(resp.status());
    const body = await resp.json();
    expect(body.access_token).toBeTruthy();
    expect(body.user.email).toBe(`api-${UNIQUE}@test.agenticorg.local`);
    expect(body.tenant.name).toBe(`API Test Org ${UNIQUE}`);
  });

  test("POST /auth/signup rejects duplicate email", async ({ request }) => {
    const resp = await request.post(`${APP}/api/v1/auth/signup`, {
      data: {
        org_name: "Dup Org",
        admin_name: "Dup Admin",
        admin_email: "ceo@agenticorg.local",
        password: "DupTest123!",
      },
    });
    expect(resp.status()).toBe(409);
  });

  test("Login returns onboarding_complete field", async ({ request }) => {
    const resp = await request.post(`${APP}/api/v1/auth/login`, {
      data: {
        email: "ceo@agenticorg.local",
        password: "ceo123!",
      },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.user).toHaveProperty("onboarding_complete");
  });
});

// ═══════════════════════════════════════════════════════════
// 4. ORG MANAGEMENT API
// ═══════════════════════════════════════════════════════════

test.describe("Org Management API", () => {
  let token: string;

  test.beforeAll(async ({ request }) => {
    const resp = await request.post(`${APP}/api/v1/auth/login`, {
      data: { email: "ceo@agenticorg.local", password: "ceo123!" },
    });
    const body = await resp.json();
    token = body.access_token;
  });

  test("GET /org/profile returns tenant info", async ({ request }) => {
    const resp = await request.get(`${APP}/api/v1/org/profile`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.name).toBeTruthy();
    expect(body.slug).toBeTruthy();
  });

  test("GET /org/members returns team list", async ({ request }) => {
    const resp = await request.get(`${APP}/api/v1/org/members`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    const members = Array.isArray(body) ? body : body.members || body.items || [];
    expect(members.length).toBeGreaterThan(0);
    expect(members[0]).toHaveProperty("email");
    expect(members[0]).toHaveProperty("role");
  });

  test("POST /org/invite sends invitation", async ({ request }) => {
    const resp = await request.post(`${APP}/api/v1/org/invite`, {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        email: `invite-${UNIQUE}@test.agenticorg.local`,
        name: "Invited User",
        role: "cfo",
        domain: "finance",
      },
    });
    // Should be 200 or 201
    expect([200, 201]).toContain(resp.status());
    const body = await resp.json();
    expect(body.status).toBe("invited");
  });

  test("PUT /org/onboarding updates state", async ({ request }) => {
    const resp = await request.put(`${APP}/api/v1/org/onboarding`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { step: 2, complete: false },
    });
    expect(resp.status()).toBe(200);
  });
});

// ═══════════════════════════════════════════════════════════
// 5. SLA MONITOR PAGE
// ═══════════════════════════════════════════════════════════

test.describe("SLA Monitor", () => {
  test("SLA page loads for admin", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await page.fill('input[type="email"]', "ceo@agenticorg.local");
    await page.fill('input[type="password"]', "ceo123!");
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/dashboard/, { timeout: 15000 });

    await page.goto(`${APP}/dashboard/sla`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.getByText(/uptime|SLA|latency/i).first()).toBeVisible({ timeout: 10000 });
  });
});

// ═══════════════════════════════════════════════════════════
// 6. EVIDENCE EXPORT
// ═══════════════════════════════════════════════════════════

test.describe("Evidence Export", () => {
  test("Audit page has export buttons", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await page.fill('input[type="email"]', "ceo@agenticorg.local");
    await page.fill('input[type="password"]', "ceo123!");
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/dashboard/, { timeout: 15000 });

    await page.goto(`${APP}/dashboard/audit`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);

    // Should have export buttons
    const jsonBtn = page.getByRole("button", { name: /export|evidence|json/i }).first();
    const csvBtn = page.getByRole("button", { name: /csv/i }).first();

    const hasJson = await jsonBtn.isVisible({ timeout: 5000 }).catch(() => false);
    const hasCsv = await csvBtn.isVisible({ timeout: 5000 }).catch(() => false);
    expect(hasJson || hasCsv).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════
// 7. PLAYGROUND
// ═══════════════════════════════════════════════════════════

test.describe("Playground", () => {
  test("Playground loads and shows use cases", async ({ page }) => {
    await page.goto(`${APP}/playground`, { waitUntil: "networkidle" });
    await expect(page.getByText("Agent Playground")).toBeVisible({ timeout: 10000 });
    // Should show use case cards
    await expect(page.getByText(/Process Invoice|Reconcile/i).first()).toBeVisible({ timeout: 5000 });
  });

  test("Clicking a use case runs an agent", async ({ page }) => {
    test.setTimeout(60000);
    await page.goto(`${APP}/playground`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);

    // Click the first use case
    const firstCard = page.getByText("Process Invoice").first();
    if (await firstCard.isVisible()) {
      await firstCard.click();
      // Should show running indicator
      await page.waitForTimeout(3000);
      // Should eventually show trace output
      await expect(page.getByText(/starting|reasoning|LLM|complete/i).first()).toBeVisible({ timeout: 30000 });
    }
  });
});

// ═══════════════════════════════════════════════════════════
// 8. PRICING PAGE
// ═══════════════════════════════════════════════════════════

test.describe("Pricing", () => {
  test("Pricing page loads with three tiers", async ({ page }) => {
    await page.goto(`${APP}/pricing`, { waitUntil: "networkidle" });
    await expect(page.getByText(/free|starter/i).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/pro/i).first()).toBeVisible();
    await expect(page.getByText(/enterprise/i).first()).toBeVisible();
  });
});

// ═══════════════════════════════════════════════════════════
// 9. EVALS PAGE
// ═══════════════════════════════════════════════════════════

test.describe("Evals", () => {
  test("Evals page loads with agent scores", async ({ page }) => {
    await page.goto(`${APP}/evals`, { waitUntil: "networkidle" });
    await page.waitForTimeout(5000);
    await expect(page.getByText("Evaluation Matrix")).toBeVisible({ timeout: 15000 });
  });
});
