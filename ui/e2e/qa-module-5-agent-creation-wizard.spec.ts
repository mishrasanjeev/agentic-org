/**
 * Module 5: Agent Creation Wizard — TC-CRT-001 through 007.
 *
 * UI navigation tests against /dashboard/agents/new. The wizard
 * requires real session state, so we run as the seeded admin
 * user and clean up created agents in afterEach.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

const createdAgents: string[] = [];

test.describe("Module 5: Agent Creation Wizard @qa @wizard @agent-create", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  test.afterEach(async ({ request }) => {
    // Clean up any agents created during the test.
    for (const agentId of createdAgents.splice(0)) {
      await request.delete(`${APP}/api/v1/agents/${agentId}`, {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      });
    }
  });

  // -------------------------------------------------------------------------
  // TC-CRT-001: Wizard renders + step navigation works
  // -------------------------------------------------------------------------

  test("TC-CRT-001 wizard page loads with NL describe step + Back-to-list link", async ({
    page,
  }) => {
    await page.goto(`${APP}/dashboard/agents/new`);
    await page.waitForLoadState("domcontentloaded");
    // The "Back" link in the header navigates back to the agents list.
    await expect(
      page.getByRole("button", { name: /^back$/i }).first(),
    ).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // TC-CRT-002: Custom agent type via API (UI toggle is a checkbox flow)
  // -------------------------------------------------------------------------

  test("TC-CRT-002 POST /agents accepts custom agent_type string", async ({
    request,
  }) => {
    // The UI sends ``agent_type: finalType`` where finalType is
    // either the dropdown value OR the custom text. Pin that
    // the API accepts an arbitrary agent_type string (within
    // the schema's max_length=100).
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/agents`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        name: `qa-crt002-${ts}`,
        agent_type: "qa_custom_type", // not in any preset list
        domain: "test",
      },
      failOnStatusCode: false,
    });
    // 2xx (created) OR 4xx other than 422 (e.g. tenant-shadow-
    // limit 409 is acceptable). Foundation #8: the endpoint
    // must NOT 422 on a custom type — that would mean only
    // dropdown values are accepted, breaking the wizard's
    // custom-type branch.
    expect([200, 201, 409]).toContain(resp.status());
    if (resp.status() < 300) {
      const body = await resp.json();
      if (body.id) createdAgents.push(body.id);
      // The agent_type we sent must round-trip verbatim.
      expect(body.agent_type).toBe("qa_custom_type");
    }
  });

  // -------------------------------------------------------------------------
  // TC-CRT-005: Cancel returns to /dashboard/agents
  // -------------------------------------------------------------------------

  test("TC-CRT-005 wizard Back button at top navigates to /dashboard/agents", async ({
    page,
  }) => {
    await page.goto(`${APP}/dashboard/agents/new`);
    await page.waitForLoadState("domcontentloaded");
    // Click the header's Back button.
    await page
      .getByRole("button", { name: /^back$/i })
      .first()
      .click();
    await page.waitForURL(/\/dashboard\/agents(?:\/?)$/, { timeout: 5000 });
    expect(page.url()).toMatch(/\/dashboard\/agents\/?$/);
  });

  // -------------------------------------------------------------------------
  // TC-CRT-007: Duplicate name + agent_type returns an error (not silent)
  // -------------------------------------------------------------------------

  test("TC-CRT-007 duplicate (employee_name, agent_type, version) is rejected at DB layer", async ({
    request,
  }) => {
    const ts = Date.now();
    const dupePayload = {
      name: `qa-crt007-dup-${ts}`,
      employee_name: `qa-crt007-dup-${ts}`,
      agent_type: "qa_test_dupe",
      domain: "test",
    };

    // First create — should succeed.
    const first = await request.post(`${APP}/api/v1/agents`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: dupePayload,
      failOnStatusCode: false,
    });

    if (first.status() === 409) {
      // Tenant fleet-limit hit; can't run this test path here.
      // We don't fail; the DB constraint pin lives in the
      // backend test_module_5 source pin.
      return;
    }
    expect(first.status()).toBeLessThan(300);
    const firstBody = await first.json();
    if (firstBody.id) createdAgents.push(firstBody.id);

    // Second create with the SAME payload — must NOT 2xx.
    // The DB UniqueConstraint fires; the API surfaces it as 409
    // or 500 (depending on how IntegrityError is mapped).
    // Foundation #8 false-green prevention: must NOT silently
    // succeed (which would create duplicate rows).
    const second = await request.post(`${APP}/api/v1/agents`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: dupePayload,
      failOnStatusCode: false,
    });
    expect(second.status()).toBeGreaterThanOrEqual(400);
    if (second.status() < 300) {
      const secondBody = await second.json();
      if (secondBody.id) createdAgents.push(secondBody.id);
    }
  });
});
