// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { expect, test } from "./helpers/test";
import { APP, authenticate, requireAuth } from "./helpers/auth";

test.describe("Recorded direct-run evidence @agent-create", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await authenticate(page);
  });

  test("a recorded direct run remains explainable after page reload", async ({ page }) => {
    const agentId = process.env.E2E_EXPLANATION_AGENT_ID;
    test.skip(!agentId, "Requires a disposable local audit fixture or an approved existing run; never spends on a model.");
    await page.goto(`${APP}/dashboard/agents/${agentId}`);
    await page.reload();
    await page.getByRole("button", { name: /Why did the agent do this/ }).click();
    await expect(page.getByText("Recorded runtime status: completed.", { exact: true })).toBeVisible();
    await expect(page.getByText("Tool-call details were not captured in this run record.", { exact: true })).toBeVisible();
    await expect(page.getByText(/No explanation yet/)).toHaveCount(0);
    await page.screenshot({ path: "test-results/direct-run-explanation.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  });

  test("manual model selection does not promise free Gemini usage", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents/new`);
    await page.getByTestId("skip-to-manual").click();
    await page.getByPlaceholder("e.g. Priya, Arjun, Maya").fill("Synthetic billing review");
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.getByPlaceholder(/You are the/).fill("Synthetic reasoning only; no external actions.");
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.getByText(/Provider billing, quotas and credential requirements apply/)).toBeVisible();
    await expect(page.getByText(/Gemini is always available|Estimated cost per 1K tokens/)).toHaveCount(0);
    await expect(page.locator('option[value="tier1"]')).toHaveText("Economy - Gemini Flash");
  });
});
