/**
 * Module 7: Workflows — TC-WF-001 through 005.
 *
 * API contract tests for /workflows + /workflows/{id}/run +
 * /workflows/runs/{id}. The actual step-by-step execution
 * happens in BackgroundTasks and depends on real agents being
 * provisioned in the test tenant; the tests here pin the
 * input-validation, RBAC, and run-trigger contracts.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 7: Workflows @qa @workflows", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-WF-001: List page returns paginated shape
  // -------------------------------------------------------------------------

  test("TC-WF-001 GET /workflows returns paginated shape", async ({ request }) => {
    const resp = await request.get(`${APP}/api/v1/workflows?per_page=5`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const k of ["items", "total", "page", "per_page", "pages"]) {
      expect(body, `missing key ${k}`).toHaveProperty(k);
    }
    expect(body.per_page).toBe(5);
  });

  test("TC-WF-001b GET /workflows?per_page=10000 silently clamps to 100", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/workflows?per_page=10000`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body.per_page).toBe(100);
  });

  // -------------------------------------------------------------------------
  // TC-WF-002: Create workflow validation
  // -------------------------------------------------------------------------

  test("TC-WF-002 POST /workflows with empty steps array returns 400", async ({
    request,
  }) => {
    const resp = await request.post(`${APP}/api/v1/workflows`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        name: `qa-wf002-${Date.now()}`,
        domain: "test",
        definition: { steps: [] }, // empty steps array
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(400);
    const body = await resp.json();
    expect(JSON.stringify(body).toLowerCase()).toContain("step");
  });

  test("TC-WF-002b POST /workflows with non-array steps returns 400", async ({
    request,
  }) => {
    const resp = await request.post(`${APP}/api/v1/workflows`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        name: `qa-wf002b-${Date.now()}`,
        domain: "test",
        definition: { steps: "not-an-array" },
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(400);
  });

  test("TC-WF-002c POST /workflows without auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.post(`${APP}/api/v1/workflows`, {
      headers: { "Content-Type": "application/json" },
      data: { name: "x", definition: { steps: [{}] } },
      failOnStatusCode: false,
    });
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-WF-003: Run workflow
  // -------------------------------------------------------------------------

  test("TC-WF-003 POST /workflows/{nonexistent}/run returns 404", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/workflows/00000000-0000-0000-0000-000000000000/run`,
      {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: { payload: {} },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("TC-WF-003b POST /workflows/{id}/run without auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/workflows/00000000-0000-0000-0000-000000000000/run`,
      {
        headers: { "Content-Type": "application/json" },
        data: {},
        failOnStatusCode: false,
      },
    );
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-WF-004: Workflow run details
  // -------------------------------------------------------------------------

  test("TC-WF-004 GET /workflows/runs/{nonexistent} returns 404", async ({
    request,
  }) => {
    const resp = await request.get(
      `${APP}/api/v1/workflows/runs/00000000-0000-0000-0000-000000000000`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("TC-WF-004b GET /workflows/runs/{id} without auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.get(
      `${APP}/api/v1/workflows/runs/00000000-0000-0000-0000-000000000000`,
      { failOnStatusCode: false },
    );
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-WF-005: HITL step (smoke — full flow needs a real workflow)
  // -------------------------------------------------------------------------

  test("TC-WF-005 GET /workflows/templates returns list (workflow primitives present)", async ({
    request,
  }) => {
    // Sanity smoke that the workflows surface is wired and
    // returns a list — the HITL step contract itself is
    // source-pinned in the backend test.
    const resp = await request.get(`${APP}/api/v1/workflows/templates`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    // 2xx (list) or 404 (no templates configured) are both
    // acceptable; NOT 5xx.
    expect(resp.status()).toBeLessThan(500);
  });
});
