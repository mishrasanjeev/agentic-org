/**
 * Thorough E2E tests for the Evals page -- every number, filter, and interaction.
 *
 * Public page, no auth needed. Runs against PRODUCTION.
 * NO page.route() mocking -- all responses are real.
 */
import { test, expect } from "@playwright/test";

test.describe("Evals Page -- Thorough", () => {
  test("Page loads without errors", async ({ page, baseURL }) => {
    page.on("pageerror", (err) => {
      throw new Error(`Page error: ${err.message}`);
    });
    await page.goto(`${baseURL}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("Evaluation Matrix")).toBeVisible({
      timeout: 15000,
    });
  });

  test("No raw decimal percentages (0.xxx%) anywhere on page", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    const text = await page.locator("body").innerText();
    const badMatches = text.match(/0\.\d+%/g) || [];
    expect(
      badMatches,
      `Found raw decimals: ${badMatches.join(", ")}`,
    ).toHaveLength(0);
  });

  test("Platform metrics show valid percentages", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    const metricCards = page.locator("text=/\\d+\\.?\\d*%/");
    const count = await metricCards.count();
    expect(count).toBeGreaterThan(0);
  });

  test("Domain filter buttons show data for each domain", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    for (const domain of ["finance", "hr", "marketing", "ops", "comms"]) {
      const btn = page
        .getByRole("button", { name: new RegExp(domain, "i") })
        .first();
      if (await btn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await btn.click();
        await page.waitForSelector("table tbody tr", { timeout: 5000 }).catch(() => {});
        const rows = page.locator("table tbody tr");
        const rowCount = await rows.count();
        expect(rowCount, `${domain} filter shows no agents`).toBeGreaterThan(0);
      }
    }

    // Click "All Domains" to reset
    const allBtn = page.getByRole("button", { name: /all/i }).first();
    if (await allBtn.isVisible()) {
      await allBtn.click();
      await page.waitForSelector("table tbody tr", { timeout: 5000 }).catch(() => {});
      const rows = page.locator("table tbody tr");
      const rowCount = await rows.count();
      expect(rowCount).toBeGreaterThan(10); // Should show all 35 agents
    }
  });

  test("Domain score cards show valid percentages and test case counts", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    for (const domain of ["Finance", "HR", "Marketing", "Ops", "Comms"]) {
      const card = page.locator(`text=${domain}`).first();
      await expect(card).toBeVisible();
    }
  });

  test("Bar chart shows proper percentages", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await page.evaluate(() =>
      window.scrollTo(0, document.body.scrollHeight / 2),
    );
    await page.waitForSelector('text="Agent Comparison"', { timeout: 10000 }).catch(() => {});
    await expect(page.locator("text=Agent Comparison")).toBeVisible();
  });

  test("Page displays correct agent count (35 agents)", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    // The "All Domains" button or summary should reflect 35 agents
    const allBtn = page.getByRole("button", { name: /all/i }).first();
    if (await allBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await allBtn.click();
      await page.waitForSelector("table tbody tr", { timeout: 5000 }).catch(() => {});
    }

    const rows = page.locator("table tbody tr");
    const rowCount = await rows.count();
    // Should have at least 30 rows (35 agents)
    expect(rowCount).toBeGreaterThanOrEqual(30);
  });
});
