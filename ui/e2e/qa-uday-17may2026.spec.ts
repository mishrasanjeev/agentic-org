import { expect, test } from "@playwright/test";

/**
 * Uday CA-Firms 2026-05-17, bug 1.
 *
 * Tester steps: register a Zoho Books connector, generate 10 shadow test
 * samples on /dashboard/agents/{id}, click Promote.
 *
 * Pre-fix actual result: HTTP 409 connector_not_ready_for_activation citing
 * income_tax_india / tally as missing_connector_config, and the structured
 * error object blanked the page (rendered as a React child).
 *
 * Expected result: the agent promotes (Zoho Books is the only required
 * connector for a CA pack agent); and any structured connector error is
 * shown as a readable, user-friendly message rather than crashing the page.
 */

const AGENT_ID = "05ca5ee1-8a00-4b48-9d25-68ceb9e232d4"; // TDS Compliance Agent

const user = {
  email: "uday.chauhan@edumatica.io",
  name: "Uday Chauhan",
  role: "admin",
  domain: "finance",
  tenant_id: "49ca24aa-c6e7-4124-91af-059023295da4",
  onboarding_complete: true,
};

function caTdsAgent(status: string) {
  return {
    id: AGENT_ID,
    name: "New Bharat Accountant Firm - TDS Compliance Agent",
    employee_name: "TDS Compliance Agent",
    agent_type: "tds_compliance_agent",
    domain: "finance",
    status,
    version: "1.0.0",
    shadow_sample_count: 10,
    shadow_min_samples: 10,
    shadow_accuracy_current: 0.97,
    shadow_accuracy_floor: 0.6,
    connector_ids: [
      "registry-zoho_books",
      "registry-income_tax_india",
      "registry-tally",
    ],
    config: { pack_install: { pack_name: "ca-firm" } },
    authorized_tools: ["zoho_books:calculate_tds"],
    llm_model: "gpt-4o",
  };
}

async function stubCommonRoutes(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/auth/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(user),
    }),
  );
  // Permissive catch-all for the page's secondary GETs (feedback,
  // amendments, history, etc.) so the detail page hydrates cleanly.
  await page.route(
    new RegExp(`/api/v1/agents/${AGENT_ID}/[a-z-]+`),
    (route) => {
      if (route.request().method() !== "GET") return route.continue();
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    },
  );
}

test("Uday 17-May: CA agent promotes on a Zoho-Books-only tenant", async ({
  page,
}) => {
  await stubCommonRoutes(page);

  let promoteCalled = false;
  let getCount = 0;
  await page.route(`**/api/v1/agents/${AGENT_ID}`, (route) => {
    if (route.request().method() === "POST") return route.continue();
    getCount += 1;
    // First load: shadow with 10 samples. After Promote: active.
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
         caTdsAgent(promoteCalled ? "active" : "shadow"),
      ),
    });
  });
  await page.route(
    `**/api/v1/agents/${AGENT_ID}/promote`,
    (route) => {
      promoteCalled = true;
      // Post-fix backend: required subset is Zoho only → success.
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          promoted: true,
          from: "shadow",
          to: "active",
        }),
      });
    },
  );

  await page.goto(`/dashboard/agents/${AGENT_ID}`);

  const promoteBtn = page.getByRole("button", { name: "Promote" });
  await expect(promoteBtn).toBeVisible();
  await promoteBtn.click();

  // Agent re-fetched and now active; promote control disables on active.
  await expect
    .poll(() => promoteCalled, { timeout: 10_000 })
    .toBe(true);
  await expect(page.getByText(/active/i).first()).toBeVisible();
  await expect(getCount).toBeGreaterThanOrEqual(2);
});

test("Uday 17-May: structured connector 409 shows a readable message, no crash", async ({
  page,
}) => {
  await stubCommonRoutes(page);

  await page.route(`**/api/v1/agents/${AGENT_ID}`, (route) => {
    if (route.request().method() === "POST") return route.continue();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(caTdsAgent("shadow")),
    });
  });
  // Simulate an environment where the gate still blocks (e.g. Zoho itself
  // unhealthy) — the structured 409 must render as readable text, never as
  // an object that blanks the page.
  await page.route(`**/api/v1/agents/${AGENT_ID}/promote`, (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          error: "connector_not_ready_for_activation",
          message:
            "Agents can become active only when all linked connectors are healthy and refreshable.",
          connectors: [
            { connector: "zoho_books", reason: "connector_health_not_healthy" },
          ],
        },
      }),
    }),
  );

  await page.goto(`/dashboard/agents/${AGENT_ID}`);

  const promoteBtn = page.getByRole("button", { name: "Promote" });
  await expect(promoteBtn).toBeVisible();
  await promoteBtn.click();

  const errorText = page.locator("p.text-destructive");
  await expect(errorText).toBeVisible();
  await expect(errorText).toContainText("zoho_books");
  await expect(errorText).toContainText("connector health not healthy");
  await expect(errorText).not.toContainText("[object Object]");
  // Page did not crash: the Promote control is still interactive.
  await expect(promoteBtn).toBeVisible();
});
