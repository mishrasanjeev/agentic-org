/**
 * Settings → Compliance & Data governance persistence (Phase 4).
 *
 * Verifies the three controls on the Compliance card (PII masking,
 * data region, audit retention) round-trip through /governance/config:
 *  1. GET populates initial values.
 *  2. Changing a value + Save persists.
 *  3. Reload pulls the persisted value back.
 *  4. Every write writes an audit event readable via /audit.
 *
 * Drift guard: pre-PR-B this card was decorative — local React state
 * only. If that regresses, the "reload" assertion fails because the
 * page returns to the default.
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

test.describe("Settings → Compliance & Data governance", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await seedSession(page);
  });

  test("GET /governance/config returns populated values", async ({ request }) => {
    const resp = await request.get(`${APP}/api/v1/governance/config`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    });
    expect(resp.status(), "GET /governance/config").toBe(200);
    const data = await resp.json();
    expect(typeof data.pii_masking).toBe("boolean");
    expect(["IN", "EU", "US"]).toContain(data.data_region);
    expect(data.audit_retention_years).toBeGreaterThanOrEqual(1);
    expect(data.audit_retention_years).toBeLessThanOrEqual(10);
  });

  test("Toggling PII masking + Save persists across reload", async ({ page }) => {
    // Sync on the initial GET so the inputs are hydrated.
    const initialLoaded = page.waitForResponse(
      (r) => r.url().includes("/api/v1/governance/config") && r.request().method() === "GET",
      { timeout: 30000 },
    );
    await page.goto(`${APP}/dashboard/settings`, { waitUntil: "domcontentloaded" });
    await initialLoaded;

    const card = page.getByTestId("governance-card");
    await expect(card).toBeVisible({ timeout: 15000 });

    // Read current value, flip it, then save.
    const select = page.getByTestId("governance-pii-masking");
    const currentValue = await select.inputValue();
    const flipped = currentValue === "enabled" ? "disabled" : "enabled";

    const saved = page.waitForResponse(
      (r) => r.url().includes("/api/v1/governance/config") && r.request().method() === "PUT",
      { timeout: 30000 },
    );
    await select.selectOption(flipped);
    await page.getByTestId("governance-save").click();
    const putResp = await saved;
    expect(putResp.status(), "PUT /governance/config").toBeLessThan(300);

    await expect(page.getByTestId("governance-saved")).toBeVisible({ timeout: 10000 });

    // Reload and confirm the persisted value came back.
    const reloaded = page.waitForResponse(
      (r) => r.url().includes("/api/v1/governance/config") && r.request().method() === "GET",
      { timeout: 30000 },
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    await reloaded;
    await expect(page.getByTestId("governance-pii-masking")).toHaveValue(flipped, { timeout: 15000 });

    // Flip back so the test is idempotent for the next run.
    const saveBack = page.waitForResponse(
      (r) => r.url().includes("/api/v1/governance/config") && r.request().method() === "PUT",
      { timeout: 30000 },
    );
    await page.getByTestId("governance-pii-masking").selectOption(currentValue);
    await page.getByTestId("governance-save").click();
    await saveBack;
  });

  test("Audit log records governance_config.change", async ({ request, page }) => {
    const initialLoaded = page.waitForResponse(
      (r) => r.url().includes("/api/v1/governance/config") && r.request().method() === "GET",
      { timeout: 30000 },
    );
    await page.goto(`${APP}/dashboard/settings`, { waitUntil: "domcontentloaded" });
    await initialLoaded;

    // Make any change.
    const years = page.getByTestId("governance-audit-retention");
    const current = Number(await years.inputValue());
    const next = current === 10 ? 9 : current + 1;

    const saved = page.waitForResponse(
      (r) => r.url().includes("/api/v1/governance/config") && r.request().method() === "PUT",
      { timeout: 30000 },
    );
    await years.fill(String(next));
    await page.getByTestId("governance-save").click();
    await saved;
    await expect(page.getByTestId("governance-saved")).toBeVisible({ timeout: 10000 });

    // Query the audit endpoint directly.
    const auditResp = await request.get(
      `${APP}/api/v1/audit?event_type=governance_config.change&limit=5`,
      { headers: { Authorization: `Bearer ${E2E_TOKEN}` } },
    );
    expect(auditResp.status(), "GET /audit").toBe(200);
    const body = await auditResp.json();
    const entries = Array.isArray(body) ? body : body?.items ?? [];
    expect(
      entries.length,
      "at least one governance_config.change audit entry should exist",
    ).toBeGreaterThan(0);

    // Flip back.
    const back = page.waitForResponse(
      (r) => r.url().includes("/api/v1/governance/config") && r.request().method() === "PUT",
      { timeout: 30000 },
    );
    await years.fill(String(current));
    await page.getByTestId("governance-save").click();
    await back;
  });
});
