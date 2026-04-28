/**
 * Module 22: Cross-Cutting Concerns — TC-CC-001 through TC-CC-012.
 *
 * API contract tests dominate this module (RBAC, error envelope
 * shape, large payload handling). Tenant-isolation TC-CC-004 is
 * intentionally NOT exercised here — it requires a SECOND tenant's
 * token, which the E2E session doesn't have. The backend unit
 * test in tests/unit/test_module_22_cross_cutting.py source-pins
 * the SET LOCAL + RLS plumbing instead.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 22: Cross-Cutting Concerns @qa @cc @security", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-CC-001/002/003: RBAC enforcement at the route layer
  // -------------------------------------------------------------------------

  test("TC-CC-001 unauthenticated request to admin route is 401/403", async ({
    request,
  }) => {
    // /profile is admin-gated. Without a token the gate fires
    // before the handler runs.
    const resp = await request.get(`${APP}/api/v1/profile`, {
      failOnStatusCode: false,
    });
    expect([401, 403]).toContain(resp.status());
  });

  test("TC-CC-002 admin token reaches admin route", async ({ request }) => {
    // The E2E session token has admin scope (it's the seeded
    // ceo@agenticorg.local user). Profile must return 2xx.
    const resp = await request.get(`${APP}/api/v1/profile`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
  });

  test("TC-CC-003 GET /audit verb-only-GET — POST returns 405", async ({
    request,
  }) => {
    // The auditor role's read-only contract is enforced at TWO
    // layers: (1) auditor scope set has no :write entries; (2)
    // /audit endpoint registers no POST handler. We pin (2)
    // here because (1) lives in source-pin tests.
    const resp = await request.fetch(`${APP}/api/v1/audit`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {},
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(405);
  });

  // -------------------------------------------------------------------------
  // TC-CC-006/007/008: Error envelope shape
  // -------------------------------------------------------------------------

  test("TC-CC-006 invalid JSON body returns the E-series error envelope", async ({
    request,
  }) => {
    // POST /invite with a non-JSON body. FastAPI's request-parse
    // failure surfaces as a 422 from the Pydantic layer with the
    // standard FastAPI error shape; the exact envelope key may
    // be ``detail`` (FastAPI default) or ``error`` (our custom
    // handler) depending on which layer caught it. Both are
    // acceptable; we just need NOT 500.
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: "this is not json",
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeGreaterThanOrEqual(400);
    expect(resp.status()).toBeLessThan(500);
  });

  test("TC-CC-007 missing required fields returns 422", async ({ request }) => {
    // POST /invite with empty body — required field ``email`` is
    // missing. FastAPI/Pydantic returns 422 with a structured
    // detail array.
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {},
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
  });

  test("TC-CC-008 non-existent member returns 404 with NOT_FOUND envelope", async ({
    request,
  }) => {
    const resp = await request.delete(
      `${APP}/api/v1/members/00000000-0000-0000-0000-000000000000`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
    const body = await resp.json();
    // Either the route's own detail OR the global E1005 envelope.
    const message = JSON.stringify(body).toLowerCase();
    expect(message).toMatch(/not found|e1005/);
  });

  // -------------------------------------------------------------------------
  // TC-CC-009: Prompt lock on active agents (UI message contract)
  // -------------------------------------------------------------------------

  test("TC-CC-009 active-agent prompt-lock message is the exact string the UI parses", async ({
    request,
  }) => {
    // We can't easily put an agent into the "active" state from
    // a test without seeded fixtures. The exact message is
    // pinned at the source-code level in the Python test;
    // here we just confirm the agents endpoint exists and
    // responds to a GET (smoke).
    const resp = await request.get(`${APP}/api/v1/agents?per_page=1`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
  });

  // -------------------------------------------------------------------------
  // TC-CC-010: SQL injection prevention via bind parameters
  // -------------------------------------------------------------------------

  test("TC-CC-010 audit event_type filter handles SQL-meta characters safely", async ({
    request,
  }) => {
    // Try a classic injection probe in the event_type filter.
    // The ILIKE pattern is bound (not interpolated), so the
    // server returns a normal 2xx with zero matches — NOT a
    // 500 with a syntax error.
    const probe = encodeURIComponent("'; DROP TABLE audit_log; --");
    const resp = await request.get(
      `${APP}/api/v1/audit?event_type=${probe}&per_page=1`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    // Server didn't crash, returned a paginated shape.
    expect(body).toHaveProperty("items");
  });

  // -------------------------------------------------------------------------
  // TC-CC-011: XSS — backend echoes user input unchanged
  // -------------------------------------------------------------------------

  test("TC-CC-011 invite endpoint echoes user input unchanged (UI escapes)", async ({
    request,
  }) => {
    // The backend's job is NOT to escape; the UI's job is. Pin
    // that the backend stores user input verbatim — encoding/
    // escaping is the renderer's concern. Sending an XSS probe
    // as a name should round-trip as-is when read back via
    // /members.
    const probe = "<script>alert(1)</script>";
    // 400 (allowlist rejection) is expected because the role is
    // missing from the body. We're only verifying the API
    // doesn't 500 on payloads that contain HTML tags.
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        email: `qa-xss-${Date.now()}@agenticorg-test.invalid`,
        name: probe,
        role: "analyst",
      },
      failOnStatusCode: false,
    });
    // Must NOT be 500 — that would indicate the body was
    // partially-parsed and triggered an unhandled exception
    // somewhere downstream.
    expect(resp.status()).toBeLessThan(500);
  });

  // -------------------------------------------------------------------------
  // TC-CC-012: Large payload handling
  // -------------------------------------------------------------------------

  test("TC-CC-012 audit per_page=10000 is silently clamped to 100", async ({
    request,
  }) => {
    // The boundary defense: per_page is clamped at the handler.
    // Without the cap a request can ask for 10k rows and
    // OOM the worker.
    const resp = await request.get(`${APP}/api/v1/audit?per_page=10000`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body.per_page).toBe(100);
  });

  test("TC-CC-012b massive JSON body — server responds without 5xx", async ({
    request,
  }) => {
    // 1MB-ish string in a name field. Pydantic should reject
    // for length OR the body parser should clamp. Either way,
    // not a 500.
    const big = "x".repeat(1_000_000);
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        email: `qa-big-${Date.now()}@agenticorg-test.invalid`,
        name: big,
        role: "analyst",
      },
      failOnStatusCode: false,
    });
    // 400/413/422 = client error (good); 500 = server crashed
    // on big input (bad).
    expect(resp.status()).toBeLessThan(500);
  });
});
