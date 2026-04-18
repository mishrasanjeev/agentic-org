/**
 * Product claims consistency — Phase 1 Truth Freeze.
 *
 * Every externally visible count/version on the marketing, app-shell, and
 * dashboard surfaces must match GET /api/v1/product-facts. Anyone who
 * hardcodes a number in JSX (including a stale cached copy) fails this
 * spec — which is the entire point.
 */
import { expect, test } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const MARKETING = process.env.MARKETING_URL || "https://agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

function requireAuth(): void {
  if (!canAuth) {
    throw new Error(
      "E2E_TOKEN is required for this spec. Set the E2E_TOKEN env var — the suite runs against production and must have credentials.",
    );
  }
}

interface ProductFacts {
  version: string;
  connector_count: number;
  agent_count: number;
  tool_count: number;
}

async function fetchFacts(request: import("@playwright/test").APIRequestContext): Promise<ProductFacts> {
  const resp = await request.get(`${APP}/api/v1/product-facts`);
  expect(resp.status(), "GET /product-facts").toBe(200);
  const data = (await resp.json()) as ProductFacts;
  expect(data.version, "facts.version").toMatch(/^\d+\.\d+\.\d+/);
  expect(data.connector_count, "facts.connector_count").toBeGreaterThan(0);
  expect(data.agent_count, "facts.agent_count").toBeGreaterThan(0);
  expect(data.tool_count, "facts.tool_count").toBeGreaterThan(0);
  return data;
}

test.describe("Product claims consistency", () => {
  test("GET /product-facts returns populated values", async ({ request }) => {
    await fetchFacts(request);
  });

  test("Landing version pill matches /product-facts version", async ({ page, request }) => {
    const facts = await fetchFacts(request);

    await page.goto(MARKETING, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});

    const pill = page.getByTestId("landing-version-pill");
    await expect(pill).toBeVisible({ timeout: 15000 });
    await expect(pill).toContainText(`v${facts.version}`);
  });

  test("Landing hero references current connector + agent counts", async ({ page, request }) => {
    const facts = await fetchFacts(request);

    await page.goto(MARKETING, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
    // Give the useEffect fetch a beat to resolve.
    await page.waitForTimeout(1500);

    const body = (await page.locator("body").textContent()) || "";

    // Drift guard — old hardcoded counts should be gone.
    expect(body, "stale `54 native connectors` must be removed").not.toContain("54 native connectors");
    expect(body, "stale `57 native connectors` must be removed").not.toContain("57 native connectors");
    expect(body, "stale `v4.0.0` must be removed").not.toContain("v4.0.0");
    expect(body, "stale `v4.3.0` must be removed").not.toContain("v4.3.0");

    // The real current counts must actually appear somewhere.
    expect(
      body.includes(`${facts.connector_count}`),
      `body should mention real connector_count=${facts.connector_count}`,
    ).toBe(true);
  });

  test("Dashboard counts strip matches /product-facts", async ({ page, request }) => {
    requireAuth();
    const facts = await fetchFacts(request);

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

    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});

    const counts = page.getByTestId("dashboard-counts");
    await expect(counts).toBeVisible({ timeout: 15000 });
    await expect(counts).toContainText(`${facts.connector_count}`);
    await expect(counts).toContainText(`${facts.agent_count}`);
    await expect(counts).toContainText(`${facts.tool_count}`);
  });
});
