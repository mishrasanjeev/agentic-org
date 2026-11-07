// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { expect, test, type Page } from "@playwright/test";
import { initializeInsightsTheme, verifyInsightsSurface } from "./insights-visuals";

test.setTimeout(120_000);

// Browser contracts use synthetic responses; backend tenant/RLS enforcement
// has separate PostgreSQL tests. This file belongs only to the local config.
const COMPANY = "11111111-1111-4111-8111-111111111111";
const SECOND_COMPANY = "44444444-4444-4444-8444-444444444444";
const PERIOD = { from: "2026-10-01", to: "2026-10-31" };
const path = "/dashboard/insights?range=custom&from=2026-10-01&to=2026-10-31";
const pendingCompanies = new Set<() => void>();

test.afterEach(() => {
  for (const release of pendingCompanies) release();
  pendingCompanies.clear();
});

async function fixture(page: Page, baseURL: string, options: {
  role?: string;
  enabled?: boolean;
  failure?: number;
  empty?: boolean;
  delayedCompany?: boolean;
} = {}) {
  const views: URL[] = [];
  const exports: URL[] = [];
  const errors: string[] = [];
  let failure = options.failure;
  let releaseCompany!: () => void;
  const companyReady = new Promise<void>((resolve) => { releaseCompany = resolve; });
  pendingCompanies.add(releaseCompany);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(baseURL).origin) return route.abort();
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const reply = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (url.pathname.endsWith("/auth/me")) return reply({
      user_id: "22222222-2222-4222-8222-222222222222",
      tenant_id: "33333333-3333-4333-8333-333333333333",
      email: "admin@example.test", name: "Local Admin", role: options.role || "admin",
      domain: "all", onboarding_complete: true,
    });
    if (url.pathname.endsWith("/companies")) {
      if (options.delayedCompany) await companyReady;
      return reply({ items: [{ id: COMPANY, name: "Acme Example" }, { id: SECOND_COMPANY, name: "Second Example" }], total: 2 });
    }
    if (url.pathname.endsWith("/insights/status")) return reply({
      enabled: options.enabled !== false, views: ["executive", "finance"], platform_operator: false,
      settings: { timezone: "UTC", fiscal_year_start_month: 1, currency: "USD" },
    });
    if (url.pathname.includes("/insights/views/")) {
      views.push(url);
      if (failure) return reply({ detail: "Insights temporarily unavailable" }, failure);
      const metric = {
        key: "runs.volume", title: "Runs", definition: "Recorded runs", unit: "count",
        value: 1520, source: "run_facts", data_quality: "measured",
        computed_at: "2026-11-01T04:00:00Z", period: PERIOD,
        series: [{ t: "2026-10-30", value: 40 }, { t: "2026-10-31", value: 52 }],
      };
      return reply({
        view: url.pathname.split("/").at(-1), period: PERIOD,
        tiles: options.empty ? [] : [metric], charts: options.empty ? [] : [metric], needs_you: [],
      });
    }
    if (url.pathname.includes("/insights/metrics/") && url.pathname.endsWith("/export")) {
      exports.push(url);
      return route.fulfill({ status: 200, contentType: "text/csv", body: "date,value\n2026-10-30,40\n" });
    }
    return reply([]);
  });
  return { views, exports, errors, releaseCompany, recover: () => { failure = undefined; } };
}

test.beforeEach(async ({ baseURL }) => {
  const origin = new URL(baseURL!);
  expect(origin.protocol).toBe("http:");
  expect(["localhost", "127.0.0.1", "ui"]).toContain(origin.hostname);
});

for (const width of [1440, 390]) {
  for (const scheme of ["light", "dark"] as const) {
    test.describe(`${width}px ${scheme}`, () => {
      test.use({ viewport: { width, height: 1000 }, isMobile: width === 390, hasTouch: width === 390 });
      test("delayed company selection scopes views and exports", async ({ page, baseURL }, info) => {
        await initializeInsightsTheme(page, scheme);
        const state = await fixture(page, baseURL!, { delayedCompany: true });
        await page.goto(path);
        await expect(page.getByTestId("insights-tiles")).toBeVisible({ timeout: 60_000 });
        expect(state.views.at(-1)!.searchParams.get("company_id")).toBeNull();
        state.releaseCompany();
        await expect(page.getByText("Acme Example", { exact: true })).toBeVisible();
        await expect.poll(() => state.views.at(-1)?.searchParams.get("company_id")).toBe(COMPANY);
        await expect(page.getByTestId("insights-company-scope")).toBeVisible();
        await page.getByRole("combobox", { name: "Compare with", exact: true }).selectOption("none");
        await expect.poll(() => state.views.at(-1)?.searchParams.get("compare")).toBe("none");
        const chart = page.getByTestId("chart-card");
        await chart.getByRole("button", { name: "View data", exact: true }).click();
        await expect(chart.getByRole("table")).toBeVisible();
        await chart.getByRole("button", { name: /Export Runs/ }).click();
        const download = page.waitForEvent("download");
        await page.getByRole("menuitem", { name: /CSV/ }).click();
        await download;
        expect(state.exports).toHaveLength(1);
        for (const [key, value] of Object.entries({ ...PERIOD, company_id: COMPANY, compare: "none" })) {
          expect(state.exports[0].searchParams.get(key)).toBe(value);
        }
        await page.getByRole("button", { name: "Acme Example", exact: true }).click();
        await Promise.all([
          page.waitForEvent("load"),
          page.getByRole("button", { name: "Second Example", exact: true }).click(),
        ]);
        await expect(page.getByRole("button", { name: "Second Example", exact: true })).toBeVisible({ timeout: 60_000 });
        await expect.poll(() => state.views.at(-1)?.searchParams.get("company_id")).toBe(SECOND_COMPANY);
        const otherTab = await page.context().newPage();
        try {
          await otherTab.goto(`${baseURL}/robots.txt`);
          await otherTab.evaluate((id) => localStorage.setItem("company_id", id), COMPANY);
          await expect(page.getByRole("button", { name: "Acme Example", exact: true })).toBeVisible();
          await expect.poll(() => state.views.at(-1)?.searchParams.get("company_id")).toBe(COMPANY);
        } finally {
          await otherTab.close();
        }
        await expect(page.getByTestId("insights-tiles")).toBeVisible();
        await page.getByTestId("chart-card").getByRole("button", { name: "View data", exact: true }).click();
        await verifyInsightsSurface(page, page.getByRole("button", { name: "Acme Example", exact: true }), width, info, "company-scope", scheme, 0);
        await verifyInsightsSurface(page, page.getByTestId("insights-dashboard"), width, info, "insights", scheme);
        expect(state.errors).toEqual([]);
      });
    });
  }
}

test("finance role cannot select the executive view", async ({ page, baseURL }) => {
  const state = await fixture(page, baseURL!, { role: "cfo" });
  await page.goto(`${path}&view=executive`);
  await expect(page.getByTestId("insights-tiles")).toBeVisible({ timeout: 60_000 });
  expect(state.views.every((url) => url.pathname.endsWith("/finance"))).toBe(true);
  await expect(page.getByRole("tab", { name: "Executive", exact: true })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test("disabled feature makes no metric requests", async ({ page, baseURL }) => {
  const state = await fixture(page, baseURL!, { enabled: false });
  await page.goto(path);
  await expect(page.getByText("Insights is turned off", { exact: true })).toBeVisible({ timeout: 60_000 });
  expect(state.views).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("failed metric request can be retried", async ({ page, baseURL }) => {
  const state = await fixture(page, baseURL!, { failure: 503 });
  await page.goto(path);
  await expect(page.getByText("Insights temporarily unavailable", { exact: true })).toBeVisible({ timeout: 60_000 });
  state.recover();
  await page.getByRole("button", { name: /Try again|Retry/, exact: true }).click();
  await expect(page.getByTestId("insights-tiles")).toBeVisible();
  expect(state.errors).toEqual([]);
});

test("a server-side forbidden response never exposes metrics", async ({ page, baseURL }) => {
  const state = await fixture(page, baseURL!, { failure: 403 });
  await page.goto(path);
  await expect(page.getByText("No Insights view for your role", { exact: true })).toBeVisible();
  await expect(page.getByTestId("insights-tiles")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Export/ })).toHaveCount(0);
  expect(state.errors).toEqual([]);
});

test("empty data never renders an invalid number", async ({ page, baseURL }) => {
  const state = await fixture(page, baseURL!, { empty: true });
  await page.goto(path);
  await expect(page.getByText("No data yet", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("insights-dashboard")).not.toContainText(/NaN|undefined|Infinity/);
  expect(state.errors).toEqual([]);
});
