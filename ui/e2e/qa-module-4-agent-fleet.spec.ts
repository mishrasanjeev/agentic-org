/**
 * Module 4: Agent Fleet Management — TC-AGT-001 through 013
 * (012/013 are documented duplicates of 007/008).
 *
 * Mostly API contract tests against /agents/* endpoints. The
 * UI tabs themselves are exercised by the per-feature module
 * specs (Module 30 Cost tab, Module 28 hierarchy, Module 29 LLM
 * picker, etc.); this spec pins the LIST + LIFECYCLE
 * (pause/promote/rollback) contracts.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 4: Agent Fleet Management @qa @agents @fleet", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-AGT-001: Agent list returns paginated shape
  // -------------------------------------------------------------------------

  test("TC-AGT-001 GET /agents returns paginated shape", async ({ request }) => {
    const resp = await request.get(`${APP}/api/v1/agents?per_page=5`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const k of ["items", "total", "page", "per_page", "pages"]) {
      expect(body, `missing key ${k}`).toHaveProperty(k);
    }
    expect(Array.isArray(body.items)).toBe(true);
    expect(body.per_page).toBe(5);
  });

  // -------------------------------------------------------------------------
  // TC-AGT-002: Filter by domain
  // -------------------------------------------------------------------------

  test("TC-AGT-002 GET /agents?domain=Finance only returns Finance agents", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/agents?domain=Finance&per_page=10`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const a of body.items) {
      expect(a.domain).toBe("Finance");
    }
  });

  // -------------------------------------------------------------------------
  // TC-AGT-003: Filter by status
  // -------------------------------------------------------------------------

  test("TC-AGT-003 GET /agents?status=shadow only returns shadow agents", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/agents?status=shadow&per_page=10`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const a of body.items) {
      expect(a.status).toBe("shadow");
    }
  });

  // -------------------------------------------------------------------------
  // TC-AGT-005: Pause active agent
  // -------------------------------------------------------------------------

  test("TC-AGT-005 POST /agents/{nonexistent}/pause returns 404", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/pause`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("TC-AGT-005b POST /agents/{id}/pause without auth is 401/403", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/pause`,
      { failOnStatusCode: false },
    );
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-AGT-006: Agent detail (overview)
  // -------------------------------------------------------------------------

  test("TC-AGT-006 GET /agents/{nonexistent} returns 404", async ({ request }) => {
    const resp = await request.get(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  // -------------------------------------------------------------------------
  // TC-AGT-009: Promote shadow → active (failure paths)
  // -------------------------------------------------------------------------

  test("TC-AGT-009 POST /agents/{nonexistent}/promote returns 404", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/promote`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("TC-AGT-009b POST /agents/{id}/promote without auth is 401/403", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/promote`,
      { failOnStatusCode: false },
    );
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-AGT-010: Rollback (failure paths)
  // -------------------------------------------------------------------------

  test("TC-AGT-010 POST /agents/{nonexistent}/rollback returns 404", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/rollback`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("TC-AGT-010b POST /agents/{id}/rollback without auth is 401/403", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/rollback`,
      { failOnStatusCode: false },
    );
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // Org tree (BUG-28 cross-pin)
  // -------------------------------------------------------------------------

  test("GET /agents/org-tree never returns deleted/error/broken statuses", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/agents/org-tree`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    // The response shape may be {tree: [...]} or a flat list.
    // Walk whatever's there and check no node has status in
    // the excluded set.
    const nodes = Array.isArray(body) ? body : body.tree || body.agents || [];
    function walk(n: { status?: string; children?: unknown[] }) {
      if (n.status) {
        expect(["deleted", "error", "broken"]).not.toContain(n.status);
      }
      if (Array.isArray(n.children)) n.children.forEach((c) => walk(c as typeof n));
    }
    nodes.forEach(walk);
  });
});
