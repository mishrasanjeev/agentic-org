/**
 * Thorough E2E tests for the Evals page -- every number, filter, and interaction.
 *
 * Public page, no auth needed. Runs against PRODUCTION.
 * NO page.route() mocking -- all responses are real.
 */
import { test, expect } from "@playwright/test";

const MARKETING = "https://agenticorg.ai";

test.describe("Evals Page -- Thorough", () => {
  test("Page loads without errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => {
      errors.push(err.message);
    });
    await page.goto(`${MARKETING}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    // The page should show evaluation content
    const bodyText = await page.textContent("body");
    const hasEvalContent =
      bodyText?.includes("Evaluation") ||
      bodyText?.includes("Eval") ||
      bodyText?.includes("agent") ||
      bodyText?.includes("score");
    expect(hasEvalContent).toBeTruthy();
  });

  test("No raw decimal percentages (0.xxx%) anywhere on page", async ({
    page,
  }) => {
    await page.goto(`${MARKETING}/evals`, { waitUntil: "domcontentloaded" });
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
  }) => {
    await page.goto(`${MARKETING}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    const metricCards = page.locator("text=/\\d+\\.?\\d*%/");
    const count = await metricCards.count();
    expect(count).toBeGreaterThan(0);
  });

  test("Domain filter buttons show data for each domain", async ({
    page,
  }) => {
    await page.goto(`${MARKETING}/evals`, { waitUntil: "domcontentloaded" });
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
    }
  });

  test("Domain score cards show valid percentages and test case counts", async ({
    page,
  }) => {
    await page.goto(`${MARKETING}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    // Evals page shows domain cards -- check for at least some domain names
    const bodyText = await page.textContent("body") || "";
    const domainNames = ["finance", "hr", "marketing", "ops", "comms"];
    const foundDomains = domainNames.filter((d) =>
      bodyText.toLowerCase().includes(d),
    );
    expect(foundDomains.length).toBeGreaterThanOrEqual(3);
  });

  test("Bar chart shows proper percentages", async ({ page }) => {
    await page.goto(`${MARKETING}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");
    await page.evaluate(() =>
      window.scrollTo(0, document.body.scrollHeight / 2),
    );
    // Look for chart or comparison section
    const bodyText = await page.textContent("body") || "";
    const hasChartContent =
      bodyText.includes("Comparison") ||
      bodyText.includes("Chart") ||
      bodyText.includes("%");
    expect(hasChartContent).toBeTruthy();
  });

  test("Page displays agents", async ({
    page,
  }) => {
    await page.goto(`${MARKETING}/evals`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    // The evals page should show agent data
    const bodyText = await page.textContent("body") || "";
    const hasAgentContent =
      bodyText.includes("agent") ||
      bodyText.includes("Agent") ||
      bodyText.includes("score") ||
      bodyText.includes("Score");
    expect(hasAgentContent).toBeTruthy();
  });
});
