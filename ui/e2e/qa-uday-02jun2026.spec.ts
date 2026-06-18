/**
 * Uday 2026-06-02 CA/Marketing regression coverage.
 *
 * Hermetic UI checks: API routes are mocked so the selected-company behavior
 * is verified without hitting production data.
 */
import { expect, test, type Page, type Route } from "@playwright/test";

import { APP, setSessionToken } from "./helpers/auth";

const COMPANY_A = "company-a";
const COMPANY_B = "company-b";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockApi(page: Page) {
  let seenAgentsCompanyId = "";
  let seenImportCompanyId = "";

  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path.endsWith("/api/v1/auth/me")) {
      return json(route, {
        email: "qa@agenticorg.ai",
        name: "QA Admin",
        role: "admin",
        domain: "all",
        tenant_id: "58483c90-494b-445d-85c6-245a727fe372",
        onboarding_complete: true,
      });
    }

    if (path.endsWith("/api/v1/companies")) {
      return json(route, {
        items: [
          { id: COMPANY_A, name: "Company A" },
          { id: COMPANY_B, name: "Company B" },
        ],
        total: 2,
        page: 1,
        per_page: 100,
        pages: 1,
      });
    }

    if (path.endsWith("/api/v1/agents/import-csv")) {
      seenImportCompanyId = url.searchParams.get("company_id") || "";
      return json(route, {
        imported: 1,
        skipped: 0,
        parent_links_set: 0,
        agents: [
          {
            id: "imported-agent",
            company_id: seenImportCompanyId,
            name: "Imported Company B Agent",
            agent_type: "ap_processor",
            domain: "finance",
          },
        ],
        skip_details: [],
      });
    }

    if (path.endsWith("/api/v1/agents")) {
      seenAgentsCompanyId = url.searchParams.get("company_id") || "";
      const items = seenAgentsCompanyId === COMPANY_B
        ? [
            {
              id: "agent-b",
              company_id: COMPANY_B,
              name: "Company B AP Agent",
              employee_name: "Company B AP Agent",
              agent_type: "ap_processor",
              domain: "finance",
              status: "active",
              version: "1.0.0",
              confidence_floor: 0.88,
              shadow_sample_count: 0,
              shadow_accuracy_current: null,
              created_at: "2026-06-02T00:00:00Z",
            },
          ]
        : [
            {
              id: "agent-a",
              company_id: COMPANY_A,
              name: "Company A Agent Should Not Render",
              employee_name: "Company A Agent Should Not Render",
              agent_type: "ap_processor",
              domain: "finance",
              status: "active",
              version: "1.0.0",
              confidence_floor: 0.88,
              shadow_sample_count: 0,
              shadow_accuracy_current: null,
              created_at: "2026-06-02T00:00:00Z",
            },
          ];
      return json(route, {
        items,
        total: items.length,
        page: 1,
        per_page: 100,
        pages: 1,
      });
    }

    return json(route, { items: [] });
  });

  await setSessionToken(page, "fake.e2e.session");
  await page.addInitScript((companyId) => {
    window.localStorage.setItem("company_id", companyId);
  }, COMPANY_B);

  return {
    seenAgentsCompanyId: () => seenAgentsCompanyId,
    seenImportCompanyId: () => seenImportCompanyId,
  };
}

test.describe("Uday 02 Jun selected-company regressions", () => {
  test("Agents page requests and renders only the selected company", async ({ page }) => {
    const api = await mockApi(page);

    await page.goto(`${APP}/dashboard/agents`, {
      waitUntil: "domcontentloaded",
    });

    const main = page.locator("main");
    await expect(main).toContainText("Company B AP Agent");
    await expect(main).not.toContainText("Company A Agent Should Not Render");
    expect(api.seenAgentsCompanyId()).toBe(COMPANY_B);

    await page.getByRole("button", { name: "Import CSV" }).click();
    await page.locator('input[type="file"]').setInputFiles({
      name: "agents.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("name,agent_type,domain\nImported Company B Agent,ap_processor,finance\n"),
    });
    await page.getByRole("button", { name: "Upload & Import" }).click();
    await expect.poll(() => api.seenImportCompanyId()).toBe(COMPANY_B);
  });
});
