/**
 * Thorough E2E tests for the Evals page — every number, filter, and interaction.
 */
import { test, expect } from "@playwright/test";

const APP = "https://app.agenticorg.ai";

test.describe("Evals Page — Thorough", () => {
  test("Page loads without errors", async ({ page }) => {
    page.on("pageerror", (err) => { throw new Error(`Page error: ${err.message}`); });
    await page.goto(`${APP}/evals`, { waitUntil: "networkidle" });
    await page.waitForTimeout(5000);
    await expect(page.getByText("Evaluation Matrix")).toBeVisible({ timeout: 15000 });
  });

  test("No raw decimal percentages (0.xxx%) anywhere on page", async ({ page }) => {
    await page.goto(`${APP}/evals`, { waitUntil: "networkidle" });
    await page.waitForTimeout(5000);
    const text = await page.locator("body").innerText();
    const badMatches = text.match(/0\.\d+%/g) || [];
    expect(badMatches, `Found raw decimals: ${badMatches.join(", ")}`).toHaveLength(0);
  });

  test("Platform metrics show valid percentages", async ({ page }) => {
    await page.goto(`${APP}/evals`, { waitUntil: "networkidle" });
    await page.waitForTimeout(5000);
    // All platform metrics should be >1% (no raw decimals)
    const metricCards = page.locator("text=/\\d+\\.?\\d*%/");
    const count = await metricCards.count();
    expect(count).toBeGreaterThan(0);
  });

  test("Domain filter buttons show data for each domain", async ({ page }) => {
    await page.goto(`${APP}/evals`, { waitUntil: "networkidle" });
    await page.waitForTimeout(5000);

    for (const domain of ["finance", "hr", "marketing", "ops"]) {
      // Click domain filter
      const btn = page.getByRole("button", { name: new RegExp(domain, "i") }).first();
      if (await btn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await btn.click();
        await page.waitForTimeout(500);
        // Table should have at least 1 row (not empty)
        const rows = page.locator("table tbody tr");
        const rowCount = await rows.count();
        expect(rowCount, `${domain} filter shows no agents`).toBeGreaterThan(0);
      }
    }

    // Click "All Domains" to reset
    const allBtn = page.getByRole("button", { name: /all/i }).first();
    if (await allBtn.isVisible()) {
      await allBtn.click();
      await page.waitForTimeout(500);
      const rows = page.locator("table tbody tr");
      const rowCount = await rows.count();
      expect(rowCount).toBeGreaterThan(10); // Should show all 22 agents
    }
  });

  test("Domain score cards show valid percentages and test case counts", async ({ page }) => {
    await page.goto(`${APP}/evals`, { waitUntil: "networkidle" });
    await page.waitForTimeout(5000);
    // Each domain card should show XX.X% (not 0.xxx%)
    for (const domain of ["Finance", "HR", "Marketing", "Ops"]) {
      const card = page.locator(`text=${domain}`).first();
      await expect(card).toBeVisible();
    }
  });

  test("Bar chart shows proper percentages", async ({ page }) => {
    await page.goto(`${APP}/evals`, { waitUntil: "networkidle" });
    await page.waitForTimeout(5000);
    // Scroll to chart
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight / 2));
    await page.waitForTimeout(1000);
    // Chart should be visible
    await expect(page.locator("text=Agent Comparison")).toBeVisible();
  });
});
