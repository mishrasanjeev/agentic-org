// SPDX-License-Identifier: Apache-2.0
/**
 * Production follow-up 2026-09-15 (post-deploy run 34882967171).
 *
 * The approvals "no NaN" checks read `main.textContent`, which includes the
 * collapsed "Reasoning Trace" JSON: verbatim agent output. Agent prose can
 * legitimately say "NaN" or "undefined". The display-quality checks now read
 * rendered text. This spec pins both halves: a real rendering defect would
 * still be caught, and the verbatim payload stays available to reviewers.
 */
import { expect, type Page, test } from "@playwright/test";

const AGENT_TEXT = "Variance is NaN because the PO total is undefined in the source ledger.";

async function installRoutes(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        json: {
          email: "qa@example.com",
          name: "QA",
          role: "admin",
          domain: "all",
          tenant_id: "tenant-1",
          org_name: "QA Org",
          onboarding_complete: true,
        },
      });
      return;
    }
    if (path.endsWith("/approvals")) {
      const status = url.searchParams.get("status");
      const items =
        status === "pending"
          ? [
              {
                id: "hitl-1",
                title: "HITL: ap_processor — confidence 0.000 < floor 0.88",
                trigger_type: "confidence_below_floor",
                priority: "high",
                status: "pending",
                assignee_role: "finance",
                expires_at: new Date(Date.now() + 3_600_000).toISOString(),
                context: { confidence: 0, output: { summary: AGENT_TEXT, variance: null } },
              },
            ]
          : [];
      await route.fulfill({ json: { items, total: items.length, page: 1, per_page: 50, pages: 1 } });
      return;
    }
    await route.fulfill({ json: {} });
  });
}

test.describe("Approvals render agent payloads verbatim without display defects", () => {
  test("rendered text has no NaN/undefined; the collapsed trace keeps the agent's words", async ({
    page,
    baseURL,
  }) => {
    await installRoutes(page);
    await page.goto(`${baseURL}/dashboard/approvals`, { waitUntil: "domcontentloaded" });
    const main = page.locator("main");
    await expect(main.getByText("Approval Queue")).toBeVisible({ timeout: 15000 });
    await expect(main.getByText("HITL: ap_processor", { exact: false })).toBeVisible();
    await expect(main.getByText("1 pending")).toBeVisible();

    // What a reviewer sees: counters, titles and badges are all well-formed.
    const rendered = await main.innerText();
    expect(rendered).not.toContain("NaN");
    expect(rendered).not.toContain("undefined");

    // textContent includes the collapsed JSON; that is data, not a render bug.
    expect((await main.textContent()) || "").toContain(AGENT_TEXT);

    // Expanding the trace shows the agent output verbatim.
    await main.getByText("Reasoning Trace").click();
    await expect(main.locator("pre")).toContainText(AGENT_TEXT);
  });
});
