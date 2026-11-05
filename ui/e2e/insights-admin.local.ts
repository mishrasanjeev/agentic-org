// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { expect, test, type Page } from "@playwright/test";
import { verifyInsightsSurface } from "./insights-visuals";

test.setTimeout(120_000);

const period = { from: "2026-10-26", to: "2026-11-01" };
const roleName = "bi_000000b1_abcdefghij";
const password = "synthetic-only-password-0123456789";

async function adminFixture(page: Page, baseURL: string) {
  const origin = new URL(baseURL);
  expect(origin.protocol).toBe("http:");
  expect(["127.0.0.1", "localhost", "ui"]).toContain(origin.hostname);
  const errors: string[] = [];
  const writes: string[] = [];
  const requests: URL[] = [];
  const forbiddenExports: string[] = [];
  let created = false, revoked = false;
  const connection = {
    host: "replica.example.test", port: 5432, database: "agenticorg", sslmode: "verify-ca",
    channel_binding: "require", schema: "insights_bi",
    views: ["insights_bi.run_facts", "insights_bi.daily", "insights_bi.spend_daily", "insights_bi.agents", "insights_bi.approvals"],
  };
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(baseURL).origin) return route.abort();
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const reply = (json: unknown) => route.fulfill({ status: 200, json });
    if ((url.pathname.includes("/insights/metrics/") && url.pathname.endsWith("/export"))
      || url.pathname.endsWith("/insights/exports")) {
      forbiddenExports.push(url.pathname);
      return route.abort();
    }
    if (url.pathname.endsWith("/auth/me")) return reply({
      user_id: "22222222-2222-4222-8222-222222222222", tenant_id: "33333333-3333-4333-8333-333333333333",
      email: "admin@example.test", name: "Local Admin", role: "admin", domain: "all", onboarding_complete: true,
    });
    if (url.pathname.endsWith("/companies")) return reply({ items: [], total: 0 });
    if (url.pathname.endsWith("/insights/status")) return reply({
      enabled: true, views: ["executive", "finance"], platform_operator: true, bi_access: true,
    });
    if (url.pathname.endsWith("/insights/bi-access")) {
      if (route.request().method() === "POST") {
        created = true;
        writes.push("create");
        return reply({ role_name: roleName, password, connection_string: `postgresql://${roleName}:${password}@replica.example.test:5432/agenticorg?sslmode=verify-ca&channel_binding=require`,
          created_at: "2026-11-01T10:00:00Z", ...connection });
      }
      return reply({
        connection, max_logins: 3, active_logins: created && !revoked ? 1 : 0,
        logins: created ? [{ role_name: roleName, created_at: "2026-11-01T10:00:00Z", created_by: null,
          revoked_at: revoked ? "2026-11-01T10:05:00Z" : null }] : [],
      });
    }
    if (url.pathname.endsWith(`/insights/bi-access/${roleName}`) && route.request().method() === "DELETE") {
      revoked = true;
      writes.push("revoke");
      return reply({ role_name: roleName, revoked: true, result: "dropped" });
    }
    if (url.pathname.includes("/platform/insights/")) requests.push(url);
    if (url.pathname.endsWith("/platform/insights/export")) {
      expect(route.request().method()).toBe("GET");
      expect(Object.fromEntries(url.searchParams)).toEqual({ dataset: "daily", format: "csv", ...period });
      return route.fulfill({ status: 200, contentType: "text/csv", body: "day,runs\n2026-10-30,10\n2026-10-31,4\n" });
    }
    if (url.pathname.endsWith("/platform/insights/summary")) return reply({
      period, reconciled_gate_nights: 14,
      totals: { tenants_active: 1, tenants_total: 1, runs: 14, failed: 1, failure_rate: 1 / 14, tokens: 1000, cost_usd: 1.75 },
      series: [{ day: "2026-10-30", tenants_active: 1, runs: 10, failed: 1, tokens: 900, cost_usd: 1.5 },
        { day: "2026-10-31", tenants_active: 1, runs: 4, failed: 0, tokens: 100, cost_usd: 0.25 }],
    });
    if (url.pathname.endsWith("/platform/insights/tenants")) return reply({
      period, reconciled_gate_nights: 14, truncated: false,
      tenants: [{ tenant_id: "00000000-0000-4000-8000-0000000000a3", tenant_name: "Synthetic Traders", plan: "enterprise",
        runs: 14, failed: 1, failure_rate: 1 / 14, cost_usd: 1.75, active_agents: 3, last_active_day: "2026-10-31",
        reconciled_nights: 15, gate_met: true, last_rollup_status: "reconciled" }],
    });
    if (url.pathname.endsWith("/platform/insights/posture")) return reply({
      computed_at: "2026-11-01T06:00:00Z", ok: true,
      checks: ["schema", "rls", "role", "health"].map((key) => ({ key, ok: true })),
      schema: { ok: true, database_heads: ["v6z108_insights_bi"], code_heads: ["v6z108_insights_bi"] },
      rls: { ok: true, tenant_tables: 120, forced: 120, unforced: [] },
      role: { ok: true, role: "agenticorg_app", superuser: false, bypass_rls: false },
      health: { ok: true, window_hours: 24, snapshots: 288, counts: { healthy: 288, unhealthy: 0, other: 0 },
        latest_status: "healthy", latest_at: "2026-11-01T05:55:00Z", latest_version: "9.9.9", latest_commit: "abc1234" },
    });
    return reply([]);
  });
  return { errors, writes, requests, forbiddenExports };
}

for (const width of [1440, 390]) {
  for (const scheme of ["light", "dark"] as const) {
    test.describe(`admin ${width}px ${scheme}`, () => {
      test.use({ viewport: { width, height: 1000 }, isMobile: width === 390, hasTouch: width === 390 });
      test("platform aggregate and posture views", async ({ page, baseURL }, info) => {
        await page.clock.setFixedTime(new Date("2026-11-01T06:00:00Z"));
        const state = await adminFixture(page, baseURL!);
        await page.goto("/dashboard/platform?range=7d");
        try {
          await expect(page.getByTestId("platform-tenants-table")).toBeVisible({ timeout: 60_000 });
        } finally {
          await info.attach("platform-requests", { body: JSON.stringify({ requests: state.requests.map(String), errors: state.errors }), contentType: "application/json" });
        }
        await expect(page.getByTestId("platform-tenant-row")).toContainText("Synthetic Traders");
        await expect(page.getByTestId("posture-check")).toHaveCount(4);
        await page.evaluate((color) => document.documentElement.classList.toggle("dark", color === "dark"), scheme);
        const rangedRequests = state.requests.filter((request) => !request.pathname.endsWith("/posture"));
        expect(rangedRequests).toHaveLength(2);
        for (const url of rangedRequests) {
          expect(url.searchParams.get("from")).toBe(period.from);
          expect(url.searchParams.get("to")).toBe(period.to);
        }
        const platform = page.getByTestId("platform-insights");
        await platform.getByRole("button", { name: "Export Runs per day", exact: true }).click();
        const menu = platform.getByRole("menu");
        expect(await menu.getByRole("menuitem").evaluateAll((items) => items.map((item) => item.getAttribute("data-format"))))
          .toEqual(["csv", "xlsx", "json", "parquet", "svg", "png"]);
        await expect(menu.locator('[data-format="pdf"]')).toHaveCount(0);
        const downloadReady = page.waitForEvent("download");
        await menu.locator('[data-format="csv"]').click();
        const download = await downloadReady;
        expect(download.suggestedFilename()).toBe(`platform-daily-${period.from}-${period.to}.csv`);
        expect(await download.failure()).toBeNull();
        expect(state.requests.filter((request) => request.pathname.endsWith("/platform/insights/export"))).toHaveLength(1);
        expect(state.forbiddenExports).toEqual([]);
        expect(state.writes).toEqual([]);
        await verifyInsightsSurface(page, page.getByTestId("platform-insights"), width, info, "platform");
        expect(state.errors).toEqual([]);
      });
      test("BI password dismissal and confirmed revocation", async ({ page, baseURL }, info) => {
        const state = await adminFixture(page, baseURL!);
        await page.goto("/dashboard/insights/bi-access");
        await page.getByRole("button", { name: "Create login", exact: true }).click();
        await expect(page.getByTestId("bi-password")).toHaveText(password);
        await expect(page.getByRole("button", { name: "Create login", exact: true })).toBeDisabled();
        await page.evaluate((color) => document.documentElement.classList.toggle("dark", color === "dark"), scheme);
        await verifyInsightsSurface(page, page.getByTestId("insights-bi-access"), width, info, "bi-secret");
        await page.getByRole("button", { name: "I have saved it", exact: true }).click();
        await expect(page.getByTestId("bi-secret")).toHaveCount(0);
        expect(await page.content()).not.toContain(password);
        expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain(password);
        await page.getByRole("button", { name: `Revoke ${roleName}`, exact: true }).click();
        await page.getByRole("alertdialog").getByRole("button", { name: "Cancel", exact: true }).click();
        expect(state.writes).toEqual(["create"]);
        await page.getByRole("button", { name: `Revoke ${roleName}`, exact: true }).click();
        await page.getByRole("alertdialog").getByRole("button", { name: "Yes, revoke", exact: true }).click();
        await expect(page.getByRole("status").filter({ hasText: "was revoked" })).toBeVisible();
        expect(state.writes).toEqual(["create", "revoke"]);
        await page.evaluate((color) => document.documentElement.classList.toggle("dark", color === "dark"), scheme);
        await verifyInsightsSurface(page, page.getByTestId("insights-bi-access"), width, info, "bi-access");
        expect(state.errors).toEqual([]);
      });
    });
  }
}
