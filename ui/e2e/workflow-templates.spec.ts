/**
 * Workflow template catalog drift guard — Phase 7 P7.2 (PR-C3).
 *
 * Pre-PR-C3 `ui/src/pages/Workflows.tsx` embedded a 21-entry hardcoded
 * WORKFLOW_TEMPLATES array. The backend now owns the catalog via
 * `GET /api/v1/workflows/templates` (single source:
 * `core/workflows/template_catalog.py`).
 *
 * Asserts:
 *  1. The endpoint returns items with the canonical shape.
 *  2. The Templates tab renders exactly one card per backend item.
 */
import { expect, test } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

function requireAuth(): void {
  if (!canAuth) {
    throw new Error(
      "E2E_TOKEN is required for this spec. Set the E2E_TOKEN env var — the suite runs against production and must have credentials.",
    );
  }
}

async function seedSession(page: import("@playwright/test").Page): Promise<void> {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate((t) => {
    localStorage.setItem("token", t);
    localStorage.setItem(
      "user",
      JSON.stringify({
        email: "demo@cafirm.agenticorg.ai",
        name: "Demo Partner",
        role: "admin",
        domain: "all",
        tenant_id: "58483c90-494b-445d-85c6-245a727fe372",
        onboardingComplete: true,
      }),
    );
  }, E2E_TOKEN);
}

test.describe("Workflow templates — backend-driven catalog", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await seedSession(page);
  });

  test("GET /workflows/templates returns canonical items", async ({ request }) => {
    const resp = await request.get(`${APP}/api/v1/workflows/templates`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    });
    expect(resp.status(), "GET /workflows/templates").toBe(200);
    const data = await resp.json();
    expect(Array.isArray(data?.items), "data.items array").toBe(true);
    expect(data.total, "data.total").toBeGreaterThan(0);

    const first = data.items[0];
    for (const key of ["id", "name", "description", "domain", "steps", "trigger"]) {
      expect(first, `template item missing '${key}'`).toHaveProperty(key);
    }
  });

  test("Templates tab renders one card per backend item", async ({ page, request }) => {
    const registryLoaded = page.waitForResponse(
      (r) => r.url().includes("/api/v1/workflows/templates") && r.request().method() === "GET",
      { timeout: 30000 },
    );
    await page.goto(`${APP}/dashboard/workflows`, { waitUntil: "domcontentloaded" });
    const registryResp = await registryLoaded;
    const registryData = await registryResp.json();
    const items: Array<{ id: string }> = registryData.items ?? [];

    // Switch to Templates tab.
    await page.getByRole("button", { name: /Templates/i }).first().click();

    const grid = page.getByTestId("templates-grid");
    await expect(grid).toBeVisible({ timeout: 15000 });

    const cards = page.locator('[data-testid^="use-template-"]');
    const cardCount = await cards.count();
    expect(
      cardCount,
      `UI rendered ${cardCount} cards, backend returned ${items.length}`,
    ).toBe(items.length);
  });
});
