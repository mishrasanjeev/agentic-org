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
import { expect, test } from "./helpers/test";
import { E2E_TOKEN, setSessionToken } from "./helpers/auth";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
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

  test("Connectors Marketplace never fabricates apps: real cards are non-actionable, otherwise an honest empty/error state", async ({
    page,
  }) => {
    // 2026-09-13: DEMO_MARKETPLACE_APPS was removed. With no Composio
    // catalog configured the tab must show an explicit empty or error
    // state; when a catalog is present every Connect button is disabled
    // (OAuth handoff is not wired) — never a fake "Connect (Demo)" toggle.
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "networkidle" });
    await page.getByTestId("tab-marketplace").click();

    const connect = page.locator('[data-testid^="marketplace-connect-"]').first();
    const empty = page.getByTestId("marketplace-empty");
    const error = page.getByTestId("marketplace-error");
    await expect(connect.or(empty).or(error).first()).toBeVisible({ timeout: 15_000 });

    if (await connect.count()) {
      await expect(connect).toBeDisabled();
      const label = (await connect.textContent()) || "";
      expect(label.includes("Demo"), `no demo toggles allowed, got: '${label}'`).toBe(false);
    } else {
      const shown = (await empty.isVisible()) ? empty : error;
      const text = ((await shown.textContent()) || "").trim();
      expect(text.length > 0, "empty/error state must carry a message").toBe(true);
    }
  });
});
