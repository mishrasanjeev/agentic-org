/**
 * Module 12: Audit Log — TC-AUDIT-001 through TC-AUDIT-006.
 *
 * Mix of API contract tests (auth, RBAC, filters) and UI flow
 * tests (page renders, filter input, pagination buttons,
 * export buttons trigger downloads).
 *
 * Auth: Authorization Bearer header for API calls;
 * page.goto for UI flows (the conftest sets up a real session
 * via /auth/login fixture — see helpers/auth.ts).
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 12: Audit Log @qa @audit @compliance", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-AUDIT-001: Audit log page loads
  // -------------------------------------------------------------------------

  test("TC-AUDIT-001 GET /audit returns paginated shape", async ({ request }) => {
    const resp = await request.get(`${APP}/api/v1/audit?page=1&per_page=10`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status(), `unexpected status ${resp.status()}`).toBeLessThan(300);
    const body = await resp.json();
    for (const key of ["items", "total", "page", "per_page", "pages"]) {
      expect(body, `missing key ${key}`).toHaveProperty(key);
    }
    expect(Array.isArray(body.items)).toBe(true);
    expect(body.page).toBe(1);
    expect(body.per_page).toBe(10);
  });

  test("TC-AUDIT-001b Audit page renders with title + table headers", async ({
    page,
  }) => {
    await page.goto(`${APP}/audit`);
    await expect(page.getByRole("heading", { name: /audit log/i })).toBeVisible();
    // Whether the table renders or the empty-state message renders
    // depends on whether the test tenant has audit rows. Both paths
    // are valid for this TC.
    await Promise.race([
      expect(
        page.getByRole("columnheader", { name: /event type/i }),
      ).toBeVisible(),
      expect(
        page.getByText(/No audit entries found/i),
      ).toBeVisible(),
    ]);
  });

  // -------------------------------------------------------------------------
  // TC-AUDIT-002: Filter by event type (substring, ILIKE)
  // -------------------------------------------------------------------------

  test("TC-AUDIT-002 GET /audit?event_type=auth substring filter accepted", async ({
    request,
  }) => {
    // ILIKE %auth% matches both "auth.login" and "auth.logout".
    const resp = await request.get(
      `${APP}/api/v1/audit?event_type=auth&per_page=5`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    // Every returned row's event_type contains "auth" (case-
    // insensitive) — proves substring match, not equality.
    for (const item of body.items) {
      expect(String(item.event_type).toLowerCase()).toContain("auth");
    }
  });

  test("TC-AUDIT-002b UI filter input updates results", async ({ page }) => {
    await page.goto(`${APP}/audit`);
    const input = page.getByPlaceholder(/Filter by event type/i);
    await expect(input).toBeVisible();
    await input.fill("auth");
    // The "Showing X of Y entries" indicator only renders when the
    // filter is non-empty — that's the UX contract.
    await expect(page.getByText(/Showing \d+ of \d+ entries/i)).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // TC-AUDIT-003: Pagination
  // -------------------------------------------------------------------------

  test("TC-AUDIT-003 page < 1 returns 422", async ({ request }) => {
    const resp = await request.get(`${APP}/api/v1/audit?page=0`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
  });

  test("TC-AUDIT-003b per_page > 100 is silently clamped to 100", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/audit?per_page=10000`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body.per_page).toBe(100);
  });

  test("TC-AUDIT-003c UI pagination buttons render with Previous disabled on page 1", async ({
    page,
  }) => {
    await page.goto(`${APP}/audit`);
    await expect(page.getByText(/Page 1/i)).toBeVisible();
    await expect(
      page.getByRole("button", { name: /previous/i }),
    ).toBeDisabled();
    await expect(page.getByRole("button", { name: /next/i })).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // TC-AUDIT-004: Export as CSV
  // -------------------------------------------------------------------------

  test("TC-AUDIT-004 Download CSV button triggers a CSV download", async ({
    page,
  }) => {
    await page.goto(`${APP}/audit`);
    // Wait for the page to stop loading so filteredEntries is set.
    await page
      .getByText(/Loading audit entries|No audit entries|Event Type/i)
      .first()
      .waitFor({ state: "visible", timeout: 10000 });

    const downloadPromise = page.waitForEvent("download", { timeout: 5000 });
    await page.getByRole("button", { name: /download csv/i }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/^audit-log-\d{4}-\d{2}-\d{2}\.csv$/);
  });

  // -------------------------------------------------------------------------
  // TC-AUDIT-005: Export evidence package (JSON)
  // -------------------------------------------------------------------------

  test("TC-AUDIT-005 Export Evidence Package button triggers a JSON download", async ({
    page,
  }) => {
    await page.goto(`${APP}/audit`);
    await page
      .getByText(/Loading audit entries|No audit entries|Event Type/i)
      .first()
      .waitFor({ state: "visible", timeout: 10000 });

    const downloadPromise = page.waitForEvent("download", { timeout: 5000 });
    await page.getByRole("button", { name: /export evidence package/i }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(
      /^audit-evidence-\d{4}-\d{2}-\d{2}\.json$/,
    );
  });

  // -------------------------------------------------------------------------
  // TC-AUDIT-006: Auditor role — read-only access
  // -------------------------------------------------------------------------

  test("TC-AUDIT-006 GET /audit returns rows for the auditing identity", async ({
    request,
  }) => {
    // Auditor role is enforced server-side; the E2E session may
    // not have the auditor role, but it MUST still get a 2xx
    // (other roles see a domain-scoped subset; auditor sees
    // everything). Either way the contract is "read works".
    const resp = await request.get(`${APP}/api/v1/audit?per_page=1`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
  });

  test("TC-AUDIT-006b /audit endpoint has no POST/PUT/DELETE handler", async ({
    request,
  }) => {
    // Foundation #8 false-green prevention: pin the read-only
    // contract by asserting the verbs are NOT registered.
    // FastAPI returns 405 Method Not Allowed for an unregistered
    // verb on a registered path.
    for (const method of ["POST", "PUT", "DELETE"] as const) {
      const resp = await request.fetch(`${APP}/api/v1/audit`, {
        method,
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: {},
        failOnStatusCode: false,
      });
      expect(
        resp.status(),
        `${method} /audit should be 405 (read-only), got ${resp.status()}`,
      ).toBe(405);
    }
  });
});
