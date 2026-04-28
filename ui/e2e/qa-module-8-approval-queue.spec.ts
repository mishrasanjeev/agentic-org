/**
 * Module 8: Approval Queue / HITL — TC-HITL-001 through 006.
 *
 * API contract tests for /approvals + /approvals/{id}/decide.
 * The decide flow requires real HITL items, so most behaviors
 * are source-pinned; the tests here cover the pagination
 * shape, RBAC failure paths, and decision-attribution
 * prerequisites the UI relies on.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 8: Approval Queue / HITL @qa @hitl @approvals", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-HITL-001: View pending approvals
  // -------------------------------------------------------------------------

  test("TC-HITL-001 GET /approvals returns paginated shape", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/approvals?per_page=5`, {
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

  test("TC-HITL-001b GET /approvals?per_page=10000 silently clamps to 100", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/approvals?per_page=10000`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body.per_page).toBe(100);
  });

  test("TC-HITL-001c GET /approvals default (no status param) returns only pending items", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/approvals?per_page=20`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const item of body.items) {
      expect(item.status).toBe("pending");
    }
  });

  // -------------------------------------------------------------------------
  // TC-HITL-002 / 003: Decide endpoint failure paths
  // -------------------------------------------------------------------------

  test("TC-HITL-002 POST /approvals/{nonexistent}/decide returns 404", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/approvals/00000000-0000-0000-0000-000000000000/decide`,
      {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: { decision: "approve", notes: "" },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("TC-HITL-002b POST /approvals/{id}/decide without auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/approvals/00000000-0000-0000-0000-000000000000/decide`,
      {
        headers: { "Content-Type": "application/json" },
        data: { decision: "approve" },
        failOnStatusCode: false,
      },
    );
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-HITL-004: Filter by priority
  // -------------------------------------------------------------------------

  test("TC-HITL-004 GET /approvals?priority=high returns only high-priority items", async ({
    request,
  }) => {
    const resp = await request.get(
      `${APP}/api/v1/approvals?priority=high&per_page=20`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const item of body.items) {
      expect(item.priority).toBe("high");
    }
  });

  // -------------------------------------------------------------------------
  // TC-HITL-005: Decided tab shows history
  // -------------------------------------------------------------------------

  test("TC-HITL-005 GET /approvals?status=approved returns only approved items", async ({
    request,
  }) => {
    const resp = await request.get(
      `${APP}/api/v1/approvals?status=approved&per_page=20`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const item of body.items) {
      expect(item.status).toBe("approved");
    }
  });

  test("TC-HITL-005b GET /approvals?status=rejected returns only rejected items", async ({
    request,
  }) => {
    const resp = await request.get(
      `${APP}/api/v1/approvals?status=rejected&per_page=20`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const item of body.items) {
      expect(item.status).toBe("rejected");
    }
  });

  // -------------------------------------------------------------------------
  // TC-HITL-006: Role-based visibility
  // -------------------------------------------------------------------------

  test("TC-HITL-006 GET /approvals returns items the caller can see (RBAC enforced server-side)", async ({
    request,
  }) => {
    // The E2E session has admin scope so all items come back.
    // The CFO-domain restriction is enforced server-side via
    // the Agent.domain subquery and pinned in the backend
    // source tests. Here we just confirm the response shape
    // doesn't 5xx for the test user.
    const resp = await request.get(`${APP}/api/v1/approvals`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body).toHaveProperty("items");
  });
});
