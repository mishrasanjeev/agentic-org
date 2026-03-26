import { test, expect } from "@playwright/test";

const APP = "https://app.agenticorg.ai";

const USERS = [
  { email: "ceo@agenticorg.local", pw: "ceo123!", role: "admin", name: "Sanjeev Kumar" },
  { email: "cfo@agenticorg.local", pw: "cfo123!", role: "cfo", name: "Priya Sharma" },
  { email: "chro@agenticorg.local", pw: "chro123!", role: "chro", name: "Rahul Verma" },
  { email: "cmo@agenticorg.local", pw: "cmo123!", role: "cmo", name: "Anita Desai" },
  { email: "coo@agenticorg.local", pw: "coo123!", role: "coo", name: "Vikram Patel" },
  { email: "auditor@agenticorg.local", pw: "audit123!", role: "auditor", name: "Meera Iyer" },
];

test.describe("Login & RBAC E2E", () => {
  test("Landing page has Sign In button", async ({ page }) => {
    await page.goto(APP, { waitUntil: "networkidle" });
    const signIn = page.getByText("Sign In").first();
    await expect(signIn).toBeVisible({ timeout: 10000 });
  });

  test("Unauthenticated /dashboard redirects to /login", async ({ page }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    await expect(page).toHaveURL(/\/login/);
  });

  for (const u of USERS) {
    test(`${u.role.toUpperCase()} can login: ${u.name}`, async ({ page }) => {
      await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
      await page.fill('input[type="email"]', u.email);
      await page.fill('input[type="password"]', u.pw);
      await page.click('button[type="submit"]');
      await page.waitForURL(/\/dashboard/, { timeout: 15000 });
      // Verify we're on the dashboard
      await expect(page.locator("body")).toContainText(/(Agent|Audit|Dashboard)/i);
    });
  }

  test("CFO sees only finance sidebar items", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await page.fill('input[type="email"]', "cfo@agenticorg.local");
    await page.fill('input[type="password"]', "cfo123!");
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/dashboard/, { timeout: 15000 });
    // CFO should NOT see Connectors or Settings
    await expect(page.getByText("Connectors")).not.toBeVisible();
    await expect(page.getByText("Settings")).not.toBeVisible();
    // CFO SHOULD see Agents and Approvals in sidebar nav
    await expect(page.getByRole("link", { name: "Agents" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Approvals" })).toBeVisible();
  });

  test("Auditor can only access audit log", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await page.fill('input[type="email"]', "auditor@agenticorg.local");
    await page.fill('input[type="password"]', "audit123!");
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/dashboard/, { timeout: 15000 });
    // Auditor should see Audit Log link in sidebar
    await expect(page.getByRole("link", { name: "Audit Log" })).toBeVisible();
    // Should NOT see Agents, Connectors links in sidebar
    await expect(page.getByRole("link", { name: "Agents" })).not.toBeVisible();
    await expect(page.getByRole("link", { name: "Connectors" })).not.toBeVisible();
  });

  test("Logout works", async ({ page }) => {
    await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
    await page.fill('input[type="email"]', "ceo@agenticorg.local");
    await page.fill('input[type="password"]', "ceo123!");
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/dashboard/, { timeout: 15000 });
    // Click logout
    await page.getByText("Logout").click();
    await page.waitForURL(/\/login/, { timeout: 10000 });
  });
});
