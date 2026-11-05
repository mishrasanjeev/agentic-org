// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

// Owned by e2e/insights.config.ts: the Insights journeys against the local production build with recorded API
// responses, never a live account. Programme acceptance (docs/INSIGHTS_DASHBOARD_PRD.md): each role's landing view,
// one drill to Level 4, one export per format and one scheduled report; every landing view passes a WCAG 2.2 AA scan.

const SETTINGS = { timezone: "Asia/Kolkata", fiscal_year_start_month: 4, currency: "INR" };
const PERIOD = { from: "2026-10-03", to: "2026-11-01", granularity: "day" };
const RUN_AT = "2026-11-01T03:00:00+00:00";
const WCAG_22_AA = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const LANDINGS: Array<{ role: string; view: string; tab: string; keys: string[] }> = [
  { role: "admin", view: "executive", tab: "Executive", keys: ["runs.volume", "runs.success_rate", "cost.spend"] },
  { role: "cfo", view: "finance", tab: "Finance", keys: ["cost.spend", "cost.per_run", "runs.volume"] },
  { role: "chro", view: "hr", tab: "HR", keys: ["runs.volume", "approvals.pending", "hr.pt_returns"] },
  { role: "cmo", view: "marketing", tab: "Marketing", keys: ["marketing.leads", "runs.volume"] },
  { role: "coo", view: "operations", tab: "Operations", keys: ["workflows.runs", "runs.success_rate"] },
  { role: "merchant", view: "commerce", tab: "Commerce", keys: ["commerce.orders", "commerce.webhook_failures"] },
  { role: "auditor", view: "compliance", tab: "Compliance", keys: ["compliance.audit_integrity", "compliance.dsar_open"] },
  { role: "developer", view: "engineering", tab: "Engineering", keys: ["runs.success_rate", "engineering.slow_spans"] },
  { role: "analyst", view: "domain", tab: "My domain", keys: ["runs.volume", "runs.confidence"] },
];

const VIEWS_BY_ROLE: Record<string, string[]> = {
  admin: ["executive", "finance", "operations", "hr", "marketing", "commerce", "compliance", "engineering"],
  cfo: ["finance", "partner"],
  chro: ["hr"],
  cmo: ["marketing"],
  coo: ["operations", "partner"],
  merchant: ["commerce"],
  auditor: ["compliance", "partner"],
  developer: ["engineering"],
  analyst: ["domain"],
};

function metric(key: string, extra: Record<string, unknown> = {}) {
  const percent = /rate|confidence/.test(key);
  return {
    key,
    title: key.replace(/[._]/g, " "),
    definition: `What ${key} counts.`,
    unit: percent ? "percent" : "count",
    source: "run_facts",
    data_quality: "measured",
    computed_at: "2026-11-01T10:00:00Z",
    period: PERIOD,
    value: percent ? 96.5 : 120,
    compare_value: percent ? 95 : 100,
    state: "ok",
    series: [
      { t: "2026-10-30", value: 40 },
      { t: "2026-10-31", value: 42 },
      { t: "2026-11-01", value: 38 },
    ],
    ...extra,
  };
}

function view(name: string, keys: string[]) {
  return {
    view: name,
    period: PERIOD,
    tiles: keys.map((k) => metric(k)),
    charts: [metric("runs.volume", { title: "Agent runs" })],
    needs_you: [],
  };
}

const RUN_COLUMNS = [
  { key: "started_at", title: "Started", type: "datetime", sortable: true, filter: null },
  { key: "agent", title: "Agent", type: "text", sortable: false, filter: null },
  { key: "status", title: "Status", type: "status", sortable: true, filter: "enum", options: ["completed", "failed"] },
];

interface Captured {
  posts: Array<{ path: string; body: unknown }>;
}

async function serve(page: Page, role: string, captured: Captured = { posts: [] }) {
  const origin = new URL(page.url() === "about:blank" ? (test.info().project.use.baseURL as string) : page.url()).origin;
  await page.route("**/*", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const respond = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (request.method() !== "GET") {
      captured.posts.push({ path, body: request.postDataJSON?.() ?? null });
      if (path === "/insights/exports") {
        return respond({ export: { id: "e1", kind: "view_pdf", format: "pdf", status: "queued" } }, 202);
      }
      if (path === "/report-schedules") return respond({ id: "s1" }, 201);
      return respond({});
    }
    if (path === "/auth/me") {
      return respond({
        user_id: "11111111-1111-4111-8111-111111111111", role, domain: role === "analyst" ? "finance" : "all",
        email: `${role}@example.test`, name: `Local ${role}`, onboarding_complete: true,
        tenant_id: "22222222-2222-4222-8222-222222222222",
      });
    }
    if (path === "/insights/status") {
      return respond({ enabled: true, views: VIEWS_BY_ROLE[role] ?? [], platform_operator: false, settings: SETTINGS,
                       late_data: false });
    }
    const match = path.match(/^\/insights\/views\/(\w+)$/);
    if (match) {
      const landing = LANDINGS.find((l) => l.view === match[1]);
      return respond(view(match[1], landing?.keys ?? ["runs.volume"]));
    }
    if (path === "/insights/explorers") {
      return respond({
        explorers: [{ key: "runs", title: "Runs", definition: "Every agent run.", source: ["run_facts"], columns: RUN_COLUMNS,
                      default_sort: "started_at", default_order: "desc", uses_period: true }],
      });
    }
    if (path === "/insights/explore/runs") {
      return respond({
        columns: RUN_COLUMNS,
        rows: [{ started_at: RUN_AT, agent: "Invoice matcher", status: "failed",
                 links: { run: `/dashboard/insights/runs/r1?at=${encodeURIComponent(RUN_AT)}` } }],
        total: 1, page: 1, page_size: 50, sort: "started_at", order: "desc", filters: {},
      });
    }
    if (path === "/insights/runs/r1") {
      return respond({
        run: { id: "r1", agent: "Invoice matcher", agent_id: "a1", status: "failed", started_at: RUN_AT,
               duration_ms: 9000, error_class: "TimeoutError" },
        gateway_records: [], spans: [], failure: { source: "run", status: "failed", error_class: "TimeoutError" },
        debug: null, approvals: [], audit: [], links: {}, limits: { records: 50, spans: 200 },
      });
    }
    const exported = path.match(/^\/insights\/metrics\/([\w.]+)\/export$/);
    if (exported) {
      const format = url.searchParams.get("format") ?? "csv";
      const body = format === "json" ? JSON.stringify({ rows: [{ t: "2026-11-01", value: 38 }] }) : "t,value\n2026-11-01,38\n";
      return route.fulfill({
        status: 200,
        body,
        headers: {
          "content-type": format === "json" ? "application/json" : format === "xlsx"
            ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : "text/csv",
          "content-disposition": `attachment; filename="${exported[1]}.${format}"`,
        },
      });
    }
    if (path === "/insights/changes") return respond({ since: "2026-11-01T00:00:00Z", first_visit: false, items: [] });
    if (path === "/insights/saved-views") return respond({ saved_views: [] });
    if (path === "/report-schedules") return respond([]);
    if (path === "/product-facts") return respond({ agent_count: 1, connector_count: 1, tool_count: 1, version: "test" });
    return respond([]);
  });
  return captured;
}

async function axeClean(page: Page, selector: string) {
  const results = await new AxeBuilder({ page }).include(selector).withTags(WCAG_22_AA).analyze();
  const violations = results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(violations).toEqual([]);
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("agenticorg_lang", "en"));
});

for (const landing of LANDINGS) {
  test(`${landing.role} lands on the ${landing.view} view, which passes WCAG 2.2 AA`, async ({ page }) => {
    await page.goto("/login");
    await serve(page, landing.role);
    await page.goto(`/dashboard/insights?view=${landing.view}`);
    await expect(page.getByTestId("insights-tiles")).toBeVisible();
    await expect(page.locator(`#insights-tab-${landing.view}`)).toHaveText(landing.tab);
    await axeClean(page, '[data-testid="insights-dashboard"]');
  });
}

test("a tile drills to the runs explorer and on to one run (Level 4)", async ({ page }) => {
  await page.goto("/login");
  await serve(page, "admin");
  await page.goto("/dashboard/insights?view=executive");
  await page.getByRole("link", { name: "Explore runs" }).first().click();
  await expect(page).toHaveURL(/\/dashboard\/insights\/explore\/runs/);
  await axeClean(page, '[data-testid="insights-explorer"]');
  await page.getByRole("link", { name: /Invoice matcher|2026/ }).first().click();
  await expect(page).toHaveURL(/\/dashboard\/insights\/runs\/r1/);
  await expect(page.getByTestId("run-failure")).toBeVisible();
});

test("a chart exports as CSV, XLSX and JSON, and the view asks for its PDF", async ({ page }) => {
  await page.goto("/login");
  const captured = await serve(page, "admin");
  await page.goto("/dashboard/insights?view=executive");
  for (const [label, extension] of [["CSV", "csv"], ["Excel (XLSX)", "xlsx"], ["JSON", "json"]] as const) {
    await page.getByRole("button", { name: "Export Agent runs" }).click();
    const download = page.waitForEvent("download");
    await page.getByRole("menuitem", { name: label, exact: true }).click();
    expect((await download).suggestedFilename()).toMatch(new RegExp(`\\.${extension}$`));
  }
  await page.getByTestId("view-file-pdf").click();
  await expect(page.getByText("It is being prepared", { exact: false })).toBeVisible();
  const pdf = captured.posts.find((p) => p.path === "/insights/exports");
  expect(pdf?.body).toMatchObject({ kind: "view_pdf", format: "pdf", params: { view: "executive" } });
});

test("a CFO schedules the Finance view weekly", async ({ page }) => {
  await page.goto("/login");
  const captured = await serve(page, "cfo");
  await page.goto("/dashboard/insights?view=finance");
  await page.getByTestId("view-file-schedule").click();
  await expect(page).toHaveURL(/\/dashboard\/report-schedules\?type=insights_view&view=finance/);
  await page.locator("#ch-email").check();
  await page.getByPlaceholder("recipient@company.com").fill("cfo@example.test");
  await page.getByRole("button", { name: "Create Schedule" }).click();
  await expect.poll(() => captured.posts.find((p) => p.path === "/report-schedules")).toBeTruthy();
  const body = captured.posts.find((p) => p.path === "/report-schedules")?.body as Record<string, unknown>;
  expect(body).toMatchObject({ report_type: "insights_view", params: { view: "finance" } });
  expect(String(body.cron_expression)).toMatch(/\* \* 1$/);
});
