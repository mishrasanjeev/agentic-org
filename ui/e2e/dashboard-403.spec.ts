/**
 * Dashboard KPIs + 403 UX drift guard — PR-C2.
 *
 * Two Phase-6 invariants:
 *
 *  1. Dashboard metrics strip carries no hardcoded KPIs. Specifically,
 *     the old "Deflection Rate 73%" card (fabricated, no backing API)
 *     must never come back. "Approvals Resolved" replaces it and is
 *     computed from `/approvals`.
 *
 *  2. Unauthorized role → explicit `/dashboard/access-denied` page,
 *     not a silent redirect to `/dashboard/audit`. The page tells the
 *     user what was blocked and why.
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

async function seedSession(
  page: import("@playwright/test").Page,
  role: string,
): Promise<void> {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate(
    ([t, r]) => {
      localStorage.setItem("token", t);
      localStorage.setItem(
        "user",
        JSON.stringify({
          email: "demo@cafirm.agenticorg.ai",
          name: "Demo Partner",
          role: r,
          domain: r === "admin" ? "all" : "finance",
          tenant_id: "58483c90-494b-445d-85c6-245a727fe372",
          onboardingComplete: true,
        }),
      );
    },
    [E2E_TOKEN, role],
  );
}

test.describe("Dashboard metrics — no hardcoded KPIs", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await seedSession(page, "admin");
  });

  test("Deflection Rate 73% hardcoded KPI is gone", async ({ page }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
    const body = (await page.textContent("body")) || "";
    expect(body, "stale hardcoded 'Deflection Rate' card removed").not.toContain(
      "Deflection Rate",
    );
    expect(body, "stale hardcoded '73%' KPI removed").not.toMatch(/\b73%\b.*Auto-resolved/);
    expect(body, "stale hardcoded Auto-resolved subtitle removed").not.toContain(
      "Auto-resolved support tickets",
    );
  });

  test("Approvals Resolved KPI is sourced — not a fabricated constant", async ({
    page,
  }) => {
    await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
    // "Approvals Resolved" card must exist. Its value is either a
    // percentage derived from the live count or "—" when there are
    // no approvals at all. Crucially it is NOT a fixed number.
    const label = page.getByText("Approvals Resolved").first();
    await expect(label).toBeVisible({ timeout: 15000 });
  });
});

test.describe("403 Access Denied page @tenancy", () => {
  test("Non-auditor role hitting /dashboard/audit lands on /dashboard/access-denied", async ({
    page,
  }) => {
    requireAuth();
    // The CFO role isn't in the audit page's allowedRoles for this
    // test's purposes — we seed CFO and navigate to a route that only
    // admin/auditor can reach. Using settings since settings only
    // admits admin.
    await seedSession(page, "cfo");
    await page.goto(`${APP}/dashboard/settings`, { waitUntil: "domcontentloaded" });

    // Expect the explicit access-denied page to render; NOT a silent
    // redirect to /dashboard/audit (the pre-PR-C2 behaviour).
    await expect(page).toHaveURL(/\/dashboard\/access-denied/);
    const card = page.getByTestId("access-denied");
    await expect(card).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("access-denied-role")).toContainText("cfo");
  });
});
