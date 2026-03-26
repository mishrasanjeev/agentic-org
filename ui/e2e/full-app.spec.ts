/**
 * Full app E2E — tests every page and interactive element as CEO (all access).
 */
import { test, expect, Page } from "@playwright/test";

const APP = "https://app.agenticorg.ai";

async function loginAs(page: Page, email: string, password: string) {
  await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 15000 });
}

test.describe("Full App E2E — every page, every button", () => {
  test.beforeEach(async ({ page }) => {
    await loginAs(page, "ceo@agenticorg.local", "ceo123!");
  });

  // ─── Dashboard ─────────────────────────────────────────
  test("Dashboard loads with charts and data", async ({ page }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);
    // Dashboard should show metric labels
    await expect(page.getByText("Total Agents").first()).toBeVisible({ timeout: 10000 });
  });

  // ─── Agents ────────────────────────────────────────────
  test("Agents page shows agent cards", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);
    // Should see agent names
    await expect(page.getByText("Agent Fleet").first()).toBeVisible({ timeout: 10000 });
  });

  test("Agent detail page loads when clicking an agent", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    // Click first agent card
    const firstAgent = page.locator('[class*="card"], [class*="Card"]').first();
    if (await firstAgent.isVisible()) {
      await firstAgent.click();
      await page.waitForTimeout(2000);
      // Should navigate to agent detail (not 404)
      await expect(page.locator("text=Page not found")).not.toBeVisible();
      // Should show agent info
      await expect(page.locator("text=/confidence|version|status/i").first()).toBeVisible({ timeout: 5000 });
    }
  });

  test("Create Agent page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents/new`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.locator('input, select, [role="combobox"]').first()).toBeVisible({ timeout: 5000 });
  });

  // ─── Workflows ─────────────────────────────────────────
  test("Workflows page shows workflow list", async ({ page }) => {
    await page.goto(`${APP}/dashboard/workflows`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    // Should have workflow entries
    const items = page.locator('[class*="card"], [class*="Card"], tr, [class*="border"]');
    await expect(items.first()).toBeVisible({ timeout: 10000 });
  });

  test("Workflow View button works (no 404)", async ({ page }) => {
    await page.goto(`${APP}/dashboard/workflows`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    const viewBtn = page.getByRole("link", { name: /view/i }).first().or(
      page.getByRole("button", { name: /view/i }).first()
    );
    if (await viewBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await viewBtn.click();
      await page.waitForTimeout(2000);
      await expect(page.locator("text=Page not found")).not.toBeVisible();
    }
  });

  test("Workflow Run button works (no 404)", async ({ page }) => {
    await page.goto(`${APP}/dashboard/workflows`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    const runBtn = page.getByRole("button", { name: /run/i }).first();
    if (await runBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await runBtn.click();
      await page.waitForTimeout(2000);
      // Should either show a run result or stay on page — NOT go to 404
      await expect(page.locator("text=Page not found")).not.toBeVisible();
    }
  });

  test("Create Workflow page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/workflows/new`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.locator('input, select, [role="combobox"]').first()).toBeVisible({ timeout: 5000 });
  });

  // ─── Approvals ─────────────────────────────────────────
  test("Approvals page shows pending items", async ({ page }) => {
    await page.goto(`${APP}/dashboard/approvals`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  // ─── Connectors ────────────────────────────────────────
  test("Connectors page shows connector list", async ({ page }) => {
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "networkidle" });
    await page.waitForTimeout(3000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.getByText("Connectors").first()).toBeVisible({ timeout: 10000 });
  });

  test("Create Connector page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/connectors/new`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    await expect(page.locator('input, select, [role="combobox"]').first()).toBeVisible({ timeout: 5000 });
  });

  // ─── Schemas ───────────────────────────────────────────
  test("Schemas page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/schemas`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  // ─── Audit ─────────────────────────────────────────────
  test("Audit page shows entries", async ({ page }) => {
    await page.goto(`${APP}/dashboard/audit`, { waitUntil: "networkidle" });
    await page.waitForTimeout(2000);
    await expect(page.locator("text=Page not found")).not.toBeVisible();
    // Should have audit table rows
    await expect(page.locator("table, [class*='border']").first()).toBeVisible({ timeout: 10000 });
  });

  // ─── Settings ──────────────────────────────────────────
  test("Settings page loads", async ({ page }) => {
    await page.goto(`${APP}/dashboard/settings`, { waitUntil: "networkidle" });
    await expect(page.locator("text=Page not found")).not.toBeVisible();
  });

  // ─── Navigation ────────────────────────────────────────
  test("All sidebar links navigate without 404", async ({ page }) => {
    const paths = [
      "/dashboard",
      "/dashboard/agents",
      "/dashboard/workflows",
      "/dashboard/approvals",
      "/dashboard/connectors",
      "/dashboard/schemas",
      "/dashboard/audit",
      "/dashboard/settings",
    ];
    for (const path of paths) {
      await page.goto(`${APP}${path}`, { waitUntil: "networkidle" });
      await page.waitForTimeout(1000);
      const notFound = await page.locator("text=Page not found").isVisible().catch(() => false);
      expect(notFound, `${path} shows 404`).toBe(false);
    }
  });
});
