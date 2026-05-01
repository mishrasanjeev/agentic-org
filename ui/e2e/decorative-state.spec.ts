/**
 * Decorative-state freeze (Phase 1.2).
 *
 * Every screen that previously rendered a hardcoded healthy/configured/
 * compliant badge must now either reflect a real backend source of truth
 * OR carry an explicit "Demo" / "Not configured" label. Never both,
 * never neither.
 *
 * Covers:
 *   - Settings → Grantex Integration → API Key Status:
 *       reflects /integrations/status.grantex_configured.
 *   - Connectors → Marketplace tab → Connect button:
 *       labelled as a Demo preview (UI state only, no OAuth handoff).
 *
 * Drift guard: if a regressing PR re-introduces a hardcoded "Configured"
 * dot or drops the Demo label, these assertions fail.
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
  await setSessionToken(page, E2E_TOKEN);
}

test.describe("Decorative-state freeze (P1.2)", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await seedSession(page);
  });

  test("Settings Grantex API Key Status reflects /integrations/status", async ({
    page,
    request,
  }) => {
    const statusResp = await request.get(`${APP}/api/v1/integrations/status`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    });
    expect(statusResp.status(), "GET /integrations/status").toBe(200);
    const integrations = await statusResp.json();

    await page.goto(`${APP}/dashboard/settings`, { waitUntil: "networkidle" });
    const badge = page.getByTestId("grantex-api-key-status");
    await expect(badge, "grantex status badge must render").toBeVisible();

    const text = (await badge.textContent()) || "";
    if (integrations.grantex_configured) {
      expect(
        text.toLowerCase().includes("configured"),
        `expected 'configured' in badge when grantex_configured=true, got: ${text}`,
      ).toBe(true);
    } else {
      expect(
        text.toLowerCase().includes("not configured"),
        `expected 'not configured' in badge when grantex_configured=false, got: ${text}`,
      ).toBe(true);
    }
  });

  test("Connectors Marketplace Connect button is labelled as Demo", async ({
    page,
  }) => {
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "networkidle" });
    await page.getByTestId("tab-marketplace").click();

    // Wait for marketplace cards to render. We don't assert a specific app
    // name (Composio catalog rotates) — we just need one connect button.
    const firstConnect = page
      .locator('[data-testid^="marketplace-connect-"]')
      .first();
    await expect(
      firstConnect,
      "at least one marketplace Connect button must render",
    ).toBeVisible({ timeout: 15_000 });

    const label = (await firstConnect.textContent()) || "";
    expect(
      label.includes("Demo"),
      `Connect button must be labelled as Demo, got: '${label}'`,
    ).toBe(true);
  });
});
