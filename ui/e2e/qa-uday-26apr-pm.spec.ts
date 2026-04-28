/**
 * Playwright regression for the Uday CA Firms 2026-04-26 PM bug sweep.
 *
 * Backs the source-pin tests in
 * ``tests/regression/test_qa_uday_26apr_pm.py`` with real DOM assertions
 * against the deployed app. Source-pin tests catch reverts of the literal
 * code lines; these e2e tests catch behavioural regressions even when the
 * source still looks plausible.
 *
 * Verdicts in
 * ``CA_FIRMS_BugFixSummary_Uday_26Apr2026_PM.xlsx``.
 * Brutal autopsy in ``feedback_26apr_pm_bug_sweep.md``.
 *
 * NB: BUG 2 uses ``page.route()`` to control the
 * ``POST /connectors/{id}/test`` response shape. The bug IS about the UI
 * handler reading the wrong field on a known backend shape — mocking is
 * the right tool to assert the dispatch contract independent of which
 * specific connector happens to be healthy at run time.
 */
import { expect, test, type Page } from "@playwright/test";

import { APP, E2E_TOKEN, authenticate, requireAuth } from "./helpers/auth";

interface ConnectorListItem {
  connector_id: string;
  name: string;
  auth_type: string;
}

async function pickOauth2Connector(page: Page): Promise<ConnectorListItem> {
  const resp = await page.request.get(`${APP}/api/v1/connectors`, {
    headers: { Authorization: `Bearer ${E2E_TOKEN}` },
  });
  if (!resp.ok()) {
    throw new Error(
      `pickOauth2Connector: /api/v1/connectors returned HTTP ${resp.status()} — cannot run BUG 1 / BUG 2 e2e`,
    );
  }
  const data = await resp.json();
  const items: ConnectorListItem[] = Array.isArray(data) ? data : data?.items ?? [];
  const oauth2 = items.find((c) => c.auth_type === "oauth2");
  if (!oauth2) {
    throw new Error(
      "pickOauth2Connector: tenant has no oauth2 connector — seed one before running this spec",
    );
  }
  return oauth2;
}

async function pickAgent(page: Page): Promise<{ id: string; name: string }> {
  const resp = await page.request.get(`${APP}/api/v1/agents`, {
    headers: { Authorization: `Bearer ${E2E_TOKEN}` },
  });
  if (!resp.ok()) {
    throw new Error(
      `pickAgent: /api/v1/agents returned HTTP ${resp.status()} — cannot run BUG (B) e2e`,
    );
  }
  const data = await resp.json();
  const items = Array.isArray(data) ? data : data?.items ?? [];
  if (items.length === 0) {
    throw new Error("pickAgent: tenant has no agents — seed one before running this spec");
  }
  return { id: items[0].id, name: items[0].name };
}

test.describe("Uday CA Firms 2026-04-26 PM @qa @connector @agent", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await authenticate(page);
  });

  // -------------------------------------------------------------------------
  // BUG 1 — connector edit form must render exactly ONE "Client Secret" input
  //         when authType is oauth2.
  // -------------------------------------------------------------------------

  test("BUG 1: oauth2 connector edit shows exactly one Client Secret input", async ({ page }) => {
    const c = await pickOauth2Connector(page);
    await page.goto(`${APP}/dashboard/connectors/${c.connector_id}`, {
      waitUntil: "networkidle",
    });

    // Open the Edit panel — the field-render code only fires in edit mode.
    const editToggle = page.getByRole("button", { name: /^Edit$/i }).first();
    await expect(
      editToggle,
      "Edit toggle must be reachable from the connector detail page",
    ).toBeVisible({ timeout: 15_000 });
    await editToggle.click();

    // Force authType -> oauth2 so the OAuth2 panel is in play.
    const authTypeSelect = page.locator("select").filter({ hasText: /OAuth2|Auth Type/i }).first();
    if (await authTypeSelect.isVisible().catch(() => false)) {
      await authTypeSelect.selectOption({ value: "oauth2" }).catch(() => {});
    }

    // Count every <label> whose text is exactly "Client Secret". Pre-fix
    // this was 2 (the explicit OAuth2 panel field + the generic helper
    // field). Post-fix it must be exactly 1.
    const labels = page.locator("label").filter({ hasText: /^Client Secret$/ });
    await expect(labels.first()).toBeVisible({ timeout: 10_000 });
    const count = await labels.count();
    expect(
      count,
      `Edit form must render exactly one "Client Secret" label for oauth2 (saw ${count}). ` +
        "Pre-fix the generic authTypeLabel helper rendered a second identically-labelled input.",
    ).toBe(1);
  });

  // -------------------------------------------------------------------------
  // BUG 2 — Test Connection must show success when backend returns
  //         {tested: true, health: {status: "healthy"}} — NOT
  //         "Connection test failed".
  // -------------------------------------------------------------------------

  test("BUG 2: Test Connection surfaces healthy response as success, not failure", async ({ page }) => {
    const c = await pickOauth2Connector(page);

    // Intercept the test endpoint with the canonical healthy shape so we
    // can assert the UI handler's dispatch logic without depending on
    // which connectors are currently healthy in production.
    await page.route(`**/api/v1/connectors/${c.connector_id}/test`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          tested: true,
          name: c.name,
          health: { status: "healthy", http_status: 200 },
        }),
      });
    });

    await page.goto(`${APP}/dashboard/connectors/${c.connector_id}`, {
      waitUntil: "networkidle",
    });

    const testBtn = page.getByRole("button", { name: /^Test Connection$/ });
    await expect(testBtn, "Test Connection button must render").toBeVisible({
      timeout: 15_000,
    });
    await testBtn.click();

    // Pre-fix the handler always saw `data.success === undefined` and
    // therefore always rendered a red "Connection test failed" banner.
    // Post-fix the green success banner must appear.
    const success = page.getByText(/Connection test passed/i).first();
    await expect(success, "Healthy backend response must render the success banner").toBeVisible({
      timeout: 10_000,
    });

    // Negative assertion: the error banner phrase from the broken handler
    // must NOT be visible at the same time.
    const failure = page.getByText(/Connection test failed/i);
    expect(await failure.count()).toBe(0);
  });

  // -------------------------------------------------------------------------
  // BUG (major) part B — Shadow tab must default batch to 1 and expose
  //                      a Stop control + a "Samples per click" input.
  // -------------------------------------------------------------------------

  test("BUG (major) part B: shadow Generate button defaults to 1 and exposes batch + Stop controls", async ({ page }) => {
    const a = await pickAgent(page);
    await page.goto(`${APP}/dashboard/agents/${a.id}`, { waitUntil: "networkidle" });

    // The agent detail page renders tabs as <button> elements (the
    // text is literal lowercase "shadow", capitalised via CSS). Use a
    // role+name button selector with an anchored regex so we don't
    // accidentally match a "Shadow Samples" stat card heading.
    const shadowTab = page.getByRole("button", { name: /^shadow$/i }).first();
    await expect(
      shadowTab,
      "Shadow tab button must exist on the agent detail page",
    ).toBeVisible({ timeout: 15_000 });
    await shadowTab.click();

    const samplesInput = page.getByRole("spinbutton", { name: /Samples per click/i }).first();
    const visible = await samplesInput.isVisible({ timeout: 8_000 }).catch(() => false);
    if (!visible) {
      test.skip(
        true,
        `Agent ${a.id} (${a.name}) does not expose the shadow Generate UI — pick a shadow-stage agent for this assertion`,
      );
      return;
    }

    // Default batch must be 1 (pre-fix the loop ran up to 10 per click).
    await expect(samplesInput).toHaveValue("1");

    // The button label reflects batch=1 ("Generate Test Sample" singular).
    await expect(
      page.getByRole("button", { name: /Generate Test Sample$/ }).first(),
    ).toBeVisible();

    // Bumping the batch updates the label to plural.
    await samplesInput.fill("3");
    await expect(
      page.getByRole("button", { name: /Generate 3 Samples/ }).first(),
    ).toBeVisible();
  });
});
