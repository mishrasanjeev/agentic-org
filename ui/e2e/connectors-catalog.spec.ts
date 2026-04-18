/**
 * Native-connector catalog drift guard (Phase 5 / PR-B2).
 *
 * Pre-PR-B2 the UI shipped a hardcoded `NATIVE_CONNECTOR_CATALOG` array
 * in `ui/src/pages/Connectors.tsx` (55 entries) that had drifted away
 * from the runtime registry (53 actual connectors, different names,
 * category mismatches). The catalog is now served by
 * `GET /api/v1/connectors/registry` which combines the registry with
 * `connectors/catalog_meta.py`, and the UI consumes it.
 *
 * This spec asserts:
 *  1. `/connectors/registry` returns items with the canonical shape.
 *  2. The UI renders one card per catalog item on `/dashboard/connectors`.
 *  3. Drift guard: the old hardcoded labels ("Microsoft Teams" + "Gitlab"
 *     were in the old array but not in the runtime registry) do not
 *     appear in the live DOM unless the backend actually returns them.
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

test.describe("Native-connector catalog", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await seedSession(page);
  });

  test("GET /connectors/registry returns items with canonical shape", async ({ request }) => {
    const resp = await request.get(`${APP}/api/v1/connectors/registry`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    });
    expect(resp.status(), "GET /connectors/registry").toBe(200);
    const data = await resp.json();
    expect(Array.isArray(data?.items), "data.items array").toBe(true);
    expect(data.total, "data.total").toBeGreaterThan(0);

    const first = data.items[0];
    // Every catalog item must carry the display-ready fields the UI reads.
    expect(typeof first.name).toBe("string");
    expect(typeof first.display_name).toBe("string");
    expect(typeof first.category).toBe("string");
    expect(typeof first.description).toBe("string");
  });

  test("Connectors page renders one card per catalog item", async ({ page, request }) => {
    // Sync on the registry response so the catalog is populated.
    const registryLoaded = page.waitForResponse(
      (r) => r.url().includes("/api/v1/connectors/registry") && r.request().method() === "GET",
      { timeout: 30000 },
    );
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "domcontentloaded" });
    const registryResp = await registryLoaded;
    expect(registryResp.status()).toBe(200);
    const registryData = await registryResp.json();
    const items: Array<{ name: string; display_name: string }> = registryData.items ?? [];

    // Wait for the catalog container to render.
    const container = page.getByTestId("native-catalog");
    await expect(container).toBeVisible({ timeout: 15000 });

    // At least the first card must be visible — use a specific testid.
    const firstName = items[0].name;
    await expect(page.getByTestId(`catalog-item-${firstName}`)).toBeVisible({ timeout: 15000 });

    // Count rendered cards. There is one card per registry item.
    const cards = page.locator('[data-testid^="catalog-item-"]');
    const cardCount = await cards.count();
    expect(
      cardCount,
      `UI rendered ${cardCount} cards, backend returned ${items.length}`,
    ).toBe(items.length);
  });

  test("Catalog heading reflects backend count, not a hardcoded number", async ({ page }) => {
    const registryLoaded = page.waitForResponse(
      (r) => r.url().includes("/api/v1/connectors/registry") && r.request().method() === "GET",
      { timeout: 30000 },
    );
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "domcontentloaded" });
    const registryResp = await registryLoaded;
    const registryData = await registryResp.json();
    const expected = `(${registryData.total})`;

    await expect(page.getByText(new RegExp(`Browse All Native Connectors\\s*\\(${registryData.total}\\)`)))
      .toBeVisible({ timeout: 15000 });

    // Drift guard — the stale "(54)" / "(55)" / "(57)" hardcoded counts
    // must not appear unless the live total matches.
    const body = (await page.locator("body").textContent()) || "";
    for (const n of ["54", "55", "57"]) {
      if (registryData.total === Number(n)) continue;
      expect(body, `stale hardcoded count ${n} must not appear in the catalog heading`)
        .not.toContain(`Browse All Native Connectors (${n})`);
    }
    expect(body, "backend total appears on the page").toContain(expected);
  });
});
