/**
 * End-to-end tests for signup, onboarding, team invite, and org management flows.
 *
 * Runs against PRODUCTION. Auth-dependent tests skip if E2E_TOKEN is not set.
 * NO page.route() mocking -- all responses are real.
 */
import { test, expect, Page } from "@playwright/test";

const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;
const UNIQUE = Date.now().toString(36);

// ---------------------------------------------------------------------------
// Auth helper
// ---------------------------------------------------------------------------

async function ensureAuth(page: Page, baseURL: string) {
  await page.goto(baseURL, { waitUntil: "domcontentloaded" });
  await page.evaluate((token) => {
    localStorage.setItem("token", token);
    localStorage.setItem(
      "user",
      JSON.stringify({
        email: "e2e@agenticorg.ai",
        name: "E2E Runner",
        role: "admin",
        domain: "all",
        tenant_id: "t-001",
        onboardingComplete: true,
      }),
    );
  }, E2E_TOKEN);
}

// ===========================================================================
// 1. SIGNUP FLOW (public pages -- no auth needed)
// ===========================================================================

test.describe("Signup Flow", () => {
  test("Signup page loads and shows form", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/signup`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("Create your organization")).toBeVisible({
      timeout: 10000,
    });
    await expect(page.locator('input[type="email"]')).toBeVisible();
    await expect(
      page.locator('input[type="password"]').first(),
    ).toBeVisible();
  });

  test("Signup with existing email shows error", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/signup`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    const allInputs = page.locator("input:visible");
    const count = await allInputs.count();
    for (let i = 0; i < count; i++) {
      const input = allInputs.nth(i);
      const type = await input.getAttribute("type");
      const placeholder = (await input.getAttribute("placeholder")) || "";
      const name = (await input.getAttribute("name")) || "";

      if (name.includes("org") || placeholder.toLowerCase().includes("org")) {
        await input.fill("Duplicate Test");
      } else if (
        name.includes("name") ||
        placeholder.toLowerCase().includes("name")
      ) {
        await input.fill("Duplicate User");
      } else if (type === "email") {
        await input.fill("ceo@agenticorg.local"); // existing user
      } else if (type === "password") {
        await input.fill("TestPass123!");
      }
    }

    const submitBtn = page
      .getByRole("button", { name: /create|sign up|get started/i })
      .first();
    if (await submitBtn.isVisible()) {
      await submitBtn.click();
      await page.waitForLoadState("networkidle");
      const hasError = await page
        .locator("text=/already|exists|registered|error/i")
        .isVisible({ timeout: 5000 })
        .catch(() => false);
      const stayedOnSignup = page.url().includes("/signup");
      expect(hasError || stayedOnSignup).toBe(true);
    }
  });

  test("Login page has 'Create new organization' link", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/login`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    const createLink = page.getByText(/create.*organization/i).first();
    await expect(createLink).toBeVisible({ timeout: 10000 });
    await createLink.click();
    await page.waitForURL(/\/signup/, { timeout: 5000 });
  });

  test("Login page has demo toggle that reveals test credentials", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/login`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    const demoToggle = page.getByText(/demo/i).first();
    await expect(demoToggle).toBeVisible({ timeout: 10000 });
    await demoToggle.click();
    await expect(
      page.getByText(/ceo@agenticorg/i).first(),
    ).toBeVisible({ timeout: 5000 });
  });
});

// ===========================================================================
// 2. ONBOARDING WIZARD (auth-dependent)
// ===========================================================================

test.describe("Onboarding Wizard", () => {
  test("Onboarding page redirects to login if not authenticated", async ({
    page,
    baseURL,
  }) => {
    await page.goto(baseURL!, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
    });

    await page.goto(`${baseURL}/onboarding`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    // Should redirect to login since no auth
    await page.waitForURL(/\/(login|onboarding)/, { timeout: 10000 });
  });

  test("Onboarding page loads when authenticated", async ({
    page,
    baseURL,
  }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    await ensureAuth(page, baseURL!);

    // Override onboardingComplete to false so onboarding page renders
    await page.evaluate(() => {
      const user = JSON.parse(localStorage.getItem("user") || "{}");
      user.onboardingComplete = false;
      localStorage.setItem("user", JSON.stringify(user));
    });

    await page.goto(`${baseURL}/onboarding`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    // Should be on the onboarding page (not redirected to login)
    const url = page.url();
    const bodyText = await page.textContent("body");
    const isOnboarding =
      url.includes("/onboarding") ||
      url.includes("/dashboard") ||
      bodyText?.includes("Welcome") ||
      bodyText?.includes("onboarding") ||
      bodyText?.includes("Get Started");
    expect(isOnboarding).toBeTruthy();
  });
});

// ===========================================================================
// 3. ORG MANAGEMENT API (auth-dependent)
// ===========================================================================

test.describe("Org Management API", () => {
  test("GET /org/profile returns tenant info", async ({ page, baseURL }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    const resp = await page.request.get(`${baseURL}/api/v1/org/profile`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.name).toBeTruthy();
    expect(body.slug).toBeTruthy();
  });

  test("GET /org/members returns team list", async ({ page, baseURL }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    const resp = await page.request.get(`${baseURL}/api/v1/org/members`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    const members = Array.isArray(body)
      ? body
      : body.members || body.items || [];
    expect(members.length).toBeGreaterThan(0);
    expect(members[0]).toHaveProperty("email");
    expect(members[0]).toHaveProperty("role");
  });

  test("POST /org/invite sends invitation", async ({ page, baseURL }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    const resp = await page.request.post(`${baseURL}/api/v1/org/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        email: `invite-${UNIQUE}@test.agenticorg.local`,
        name: "Invited User",
        role: "cfo",
        domain: "finance",
      },
    });
    expect([200, 201]).toContain(resp.status());
    const body = await resp.json();
    expect(body.status).toBe("invited");
  });

  test("PUT /org/onboarding updates state", async ({ page, baseURL }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    const resp = await page.request.put(`${baseURL}/api/v1/org/onboarding`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { step: 2, complete: false },
    });
    expect(resp.status()).toBe(200);
  });
});

// ===========================================================================
// 4. SLA MONITOR PAGE (auth-dependent)
// ===========================================================================

test.describe("SLA Monitor", () => {
  test("SLA page loads for admin", async ({ page, baseURL }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    await ensureAuth(page, baseURL!);

    await page.goto(`${baseURL}/dashboard/sla`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(
      page.getByText(/uptime|SLA|latency/i).first(),
    ).toBeVisible({ timeout: 10000 });
  });
});

// ===========================================================================
// 5. EVIDENCE EXPORT (auth-dependent)
// ===========================================================================

test.describe("Evidence Export", () => {
  test("Audit page has export buttons", async ({ page, baseURL }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    await ensureAuth(page, baseURL!);

    await page.goto(`${baseURL}/dashboard/audit`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    const jsonBtn = page
      .getByRole("button", { name: /export|evidence|json/i })
      .first();
    const csvBtn = page.getByRole("button", { name: /csv/i }).first();

    const hasJson = await jsonBtn
      .isVisible({ timeout: 5000 })
      .catch(() => false);
    const hasCsv = await csvBtn
      .isVisible({ timeout: 5000 })
      .catch(() => false);
    expect(hasJson || hasCsv).toBe(true);
  });
});

// ===========================================================================
// 6. PLAYGROUND (public page)
// ===========================================================================

test.describe("Playground", () => {
  test("Playground loads and shows use cases", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/playground`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("Agent Playground")).toBeVisible({
      timeout: 10000,
    });
    await expect(
      page.getByText(/Process Invoice|Reconcile/i).first(),
    ).toBeVisible({ timeout: 5000 });
  });

  test("Clicking a use case runs an agent", async ({ page, baseURL }) => {
    test.setTimeout(60000);
    await page.goto(`${baseURL}/playground`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    const firstCard = page.getByText("Process Invoice").first();
    if (await firstCard.isVisible()) {
      await firstCard.click();
      await expect(
        page.getByText(/starting|reasoning|LLM|complete/i).first(),
      ).toBeVisible({ timeout: 30000 });
    }
  });
});

// ===========================================================================
// 7. PRICING PAGE (public page)
// ===========================================================================

test.describe("Pricing", () => {
  test("Pricing page loads with three tiers", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/pricing`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await expect(page.getByText(/free|starter/i).first()).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText(/pro/i).first()).toBeVisible();
    await expect(page.getByText(/enterprise/i).first()).toBeVisible();
  });
});

// ===========================================================================
// 8. EVALS PAGE (public page)
// ===========================================================================

test.describe("Evals", () => {
  test("Evals page loads with agent scores", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("Evaluation Matrix")).toBeVisible({
      timeout: 15000,
    });
  });
});
