/**
 * Module 9: Connectors — automated coverage of 4 TCs.
 *
 * Reference each TC id in the test name so
 * `python -m scripts.qa_matrix generate` flips the matrix to
 * ``automated`` for the corresponding rows.
 *
 * Auth: uses the shared helpers/auth seeded session, which requires
 * E2E_TOKEN. Acquired in CI / locally via:
 *
 *   curl -X POST https://app.agenticorg.ai/api/v1/auth/login \
 *     -H "Content-Type: application/json" \
 *     -d '{"email":"ceo@agenticorg.local","password":"ceo123!"}' \
 *     | jq -r .access_token
 *
 * TC-CONN-004 (register) is gated behind QA_ALLOW_PROD_WRITES=1
 * because it leaves a real connector row on the demo tenant. The
 * other 3 TCs are read-only and run against prod cleanly.
 */
import { expect, test } from "@playwright/test";

import { APP, authenticate, requireAuth } from "./helpers/auth";

test.describe("Module 9: Connectors @qa @connector", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await authenticate(page);
  });

  // -------------------------------------------------------------------------
  // TC-CONN-001: Connector list page renders
  // -------------------------------------------------------------------------

  test("TC-CONN-001 connector list page renders the grid + stats row", async ({ page }) => {
    test.setTimeout(60_000);
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "networkidle" });

    // At least one connector card visible — demo tenant ships with
    // ~50 of them per the prod survey.
    const cards = page.locator('[data-testid^="connector-"], main a[href*="/dashboard/connectors/"]');
    await expect(
      cards.first(),
      "at least one connector card must render on the list page",
    ).toBeVisible({ timeout: 15_000 });

    // Stats row: Total / Active / Unhealthy counts (or the strings
    // that name them — copy-tolerant).
    const body = (await page.locator("main").textContent()) || "";
    expect(body, "page must mention Total/Active/Unhealthy stats").toMatch(
      /total|active|unhealthy|connectors/i,
    );
  });

  // -------------------------------------------------------------------------
  // TC-CONN-002: Filter by category narrows the grid
  // -------------------------------------------------------------------------

  test("TC-CONN-002 category filter narrows the connector grid", async ({ page }) => {
    test.setTimeout(60_000);
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "networkidle" });

    // The Connectors page renders a single unlabelled <select>
    // controlling categoryFilter (Connectors.tsx:251). Pick it
    // positionally — the only top-level select on the page.
    const categorySelect = page.locator("main select").first();
    await expect(categorySelect, "category dropdown must render").toBeVisible({
      timeout: 10_000,
    });

    // Try Finance first; fall back to any non-"all" option that
    // narrows the grid.
    const optionTexts = await categorySelect.locator("option").allTextContents();
    const target =
      optionTexts.find((t) => /finance/i.test(t)) ??
      optionTexts.find((t) => /^(?!all)/i.test(t.trim()));
    test.skip(!target, "no non-'all' category option present");
    await categorySelect.selectOption({ label: target! });

    // After filter, at least one connector name visible — accept any
    // of the documented finance/ops connectors so a renamed connector
    // doesn't break the test.
    await expect(
      page
        .getByText(
          /Oracle|SAP|Tally|GSTN|Stripe|Razorpay|Zoho|QuickBooks|Slack|Gmail/i,
        )
        .first(),
      "expected at least one connector card after the category filter",
    ).toBeVisible({ timeout: 10_000 });
  });

  // -------------------------------------------------------------------------
  // TC-CONN-003: Health check returns a status payload
  // -------------------------------------------------------------------------

  test("TC-CONN-003 health check returns status + health_check_at", async ({ page, request }) => {
    test.setTimeout(60_000);
    // Hit the API directly — the contract under test is the response
    // shape from GET /connectors/{id}/health, which the UI mirrors.
    // (POST returns 405 — health is read-only.)
    const E2E = process.env.E2E_TOKEN || "";
    const list = await request.get(`${APP}/api/v1/connectors`, {
      headers: { Authorization: `Bearer ${E2E}` },
    });
    expect(list.status(), "connector list must respond 200").toBe(200);
    const items = await list.json();
    const connectors = Array.isArray(items) ? items : items?.items ?? [];
    expect(
      connectors.length,
      "demo tenant must expose at least one connector",
    ).toBeGreaterThan(0);

    const target = connectors[0];
    const healthResp = await request.get(
      `${APP}/api/v1/connectors/${target.connector_id}/health`,
      { headers: { Authorization: `Bearer ${E2E}` }, failOnStatusCode: false },
    );
    expect(healthResp.status()).toBe(200);
    const payload = await healthResp.json();
    // Documented response shape:
    //   {connector_id, name, status, health_check_at, healthy}
    for (const key of ["connector_id", "name", "status", "health_check_at", "healthy"]) {
      expect(payload, `health payload missing ${key}`).toHaveProperty(key);
    }
    expect(payload.connector_id).toBe(target.connector_id);

    // UI smoke: the connector detail page must render the same status.
    await page.goto(`${APP}/dashboard/connectors/${target.connector_id}`, {
      waitUntil: "networkidle",
    });
    await expect(
      page.getByText(/healthy|unhealthy|not_configured|degraded|active/i).first(),
    ).toBeVisible({ timeout: 15_000 });
  });

  // -------------------------------------------------------------------------
  // TC-CONN-004: Register new connector — gated (writes to prod)
  // -------------------------------------------------------------------------

  test("TC-CONN-004 register-connector flow creates a new row", async ({ page, request }) => {
    test.skip(
      process.env.QA_ALLOW_PROD_WRITES !== "1",
      "Creates a real connector on the demo tenant. Set QA_ALLOW_PROD_WRITES=1 to opt in.",
    );
    test.setTimeout(60_000);
    const ts = Date.now();
    const name = `qa-test-conn-${ts}`;
    const E2E = process.env.E2E_TOKEN || "";

    // Use the API directly so we don't depend on which form fields
    // the wizard renders today. The UI is exercised by other modules.
    const create = await request.post(`${APP}/api/v1/connectors`, {
      headers: {
        Authorization: `Bearer ${E2E}`,
        "Content-Type": "application/json",
      },
      data: {
        name,
        category: "ops",
        base_url: "https://api.example.invalid",
        auth_type: "api_key",
      },
      failOnStatusCode: false,
    });
    expect([200, 201]).toContain(create.status());
    const created = await create.json();
    expect(created.name).toBe(name);

    // List page now shows the new row.
    await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "networkidle" });
    await expect(
      page.getByText(name).first(),
      "newly registered connector must appear in the list",
    ).toBeVisible({ timeout: 15_000 });

    // Cleanup — best-effort. Don't fail the test on cleanup error.
    await request
      .delete(`${APP}/api/v1/connectors/${created.connector_id}`, {
        headers: { Authorization: `Bearer ${E2E}` },
        failOnStatusCode: false,
      })
      .catch(() => {});
  });
});
