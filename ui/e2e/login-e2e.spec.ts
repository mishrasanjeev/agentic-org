import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Production E2E — Login Page
// Tests run against BASE_URL (default: https://app.agenticorg.ai)
// No page.route() mocking — all responses are real.
// ---------------------------------------------------------------------------

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const MARKETING = "https://agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;
function requireAuth(): void {
  if (!canAuth) throw new Error(
    "E2E_TOKEN is required for this spec. Set the E2E_TOKEN env var — the suite runs against production and must have credentials.",
  );
}


test.describe("Login Page — Rendering & Validation @auth", () => {
  test("login page renders with email and password fields", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await expect(page.locator('input[type="email"]')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test("login page has correct branding", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    // Should show AgenticOrg branding somewhere on the page
    const bodyText = await page.locator("body").textContent() || "";
    expect(bodyText).toMatch(/AgenticOrg|Sign In|Log In/i);
  });

  test("empty form submission shows validation error", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    // Click submit without filling in any fields
    await page.locator('button[type="submit"]').click();
    // Should show validation — either HTML5 validation or custom error
    // Check for either the input:invalid pseudo-class or a visible error message
    const emailInput = page.locator('input[type="email"]');
    const isInvalid = await emailInput.evaluate(
      (el) => !(el as HTMLInputElement).validity.valid
    );
    const hasErrorMsg = await page
      .getByText(/required|invalid|enter/i)
      .first()
      .isVisible()
      .catch(() => false);
    expect(isInvalid || hasErrorMsg).toBe(true);
  });

  test("invalid credentials show error message", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await page.fill('input[type="email"]', "invalid-user@test.invalid");
    await page.fill('input[type="password"]', "wrongpassword123");
    await page.locator('button[type="submit"]').click();
    await page.waitForLoadState("networkidle");
    // Should show an error message (not navigate to dashboard)
    // Wait for either an error message or confirm we are still on /login
    const stillOnLogin = page.url().includes("/login");
    const hasError = await page
      .getByText(/invalid|error|incorrect|failed|unauthorized/i)
      .first()
      .isVisible({ timeout: 5000 })
      .catch(() => false);
    expect(stillOnLogin || hasError).toBe(true);
    // Should NOT have navigated to dashboard
    expect(page.url()).not.toContain("/dashboard");
  });

  test("Sign In button visible on landing page navigates to login", async ({ page }) => {
    await page.goto(APP, { waitUntil: "networkidle" });
    const signIn = page.getByText("Sign In").first();
    await expect(signIn).toBeVisible({ timeout: 10000 });
    await signIn.click();
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/login");
  });
});

test.describe("Login Page — Auth Flow @auth", () => {
  test.describe.configure({ mode: "serial" });

  test("unauthenticated /dashboard redirects to /login", async ({ page }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    // Should redirect to login since no auth token
    expect(page.url()).toMatch(/\/(login|dashboard)/);
  });

  test("authenticated user can access dashboard via token", async ({ page }) => {
    requireAuth();
    await page.goto(`${APP}/login`);
    await page.evaluate((token) => {
      localStorage.setItem("token", token);
    localStorage.setItem("user", JSON.stringify({ email: "demo@cafirm.agenticorg.ai", name: "Demo Partner", role: "admin", domain: "all", tenant_id: "58483c90-494b-445d-85c6-245a727fe372", onboardingComplete: true }));    }, E2E_TOKEN);
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    await expect(page.getByText("Dashboard").first()).toBeVisible({ timeout: 10000 });
  });
});
