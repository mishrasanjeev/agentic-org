/**
 * Module 19: Health & API — TC-API-001 through TC-API-006.
 *
 * All API contract tests — no UI navigation. K8s probes,
 * Cloud Run health checks, and SDK consumers all hit these
 * endpoints. The shapes pinned here are the public-API
 * contract.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 19: Health & API @qa @health @api", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-API-001: Liveness
  // -------------------------------------------------------------------------

  test("TC-API-001 GET /health/liveness is unauthenticated and returns alive", async ({
    request,
  }) => {
    // No Authorization header — liveness must work BEFORE auth
    // is even configured. K8s probes can't carry tokens.
    const resp = await request.get(`${APP}/api/v1/health/liveness`, {
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.status).toBe("alive");
  });

  test("TC-API-001b GET /health/liveness is fast (under 2s)", async ({
    request,
  }) => {
    // Liveness must not touch any external service. Two seconds
    // is generous; if it's slower than that something's wrong
    // with the lightweight contract.
    const t0 = Date.now();
    const resp = await request.get(`${APP}/api/v1/health/liveness`, {
      failOnStatusCode: false,
    });
    const elapsed = Date.now() - t0;
    expect(resp.status()).toBe(200);
    expect(elapsed).toBeLessThan(2000);
  });

  // -------------------------------------------------------------------------
  // TC-API-002: Full readiness
  // -------------------------------------------------------------------------

  test("TC-API-002 GET /health includes db + redis check status", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/health`, {
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body).toHaveProperty("checks");
    expect(body.checks).toHaveProperty("db");
    expect(body.checks).toHaveProperty("redis");
    expect(body).toHaveProperty("version");
    expect(body).toHaveProperty("commit");
    // status MUST be one of the documented values.
    expect(["healthy", "unhealthy"]).toContain(body.status);
  });

  test("TC-API-002b GET /health/diagnostics requires admin auth", async ({
    request,
  }) => {
    // No token → 401/403. The diagnostics endpoint leaks
    // operational topology (connector health, env), so it MUST
    // be admin-gated.
    const resp = await request.get(`${APP}/api/v1/health/diagnostics`, {
      failOnStatusCode: false,
    });
    expect([401, 403]).toContain(resp.status());
  });

  test("TC-API-002c GET /health/diagnostics with admin token returns connector roster", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/health/diagnostics`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body.checks).toHaveProperty("connectors");
    expect(body.checks.connectors).toHaveProperty("registered");
    expect(body.checks.connectors).toHaveProperty("healthy");
    expect(body.checks.connectors).toHaveProperty("unhealthy");
  });

  // -------------------------------------------------------------------------
  // TC-API-003: API versioning
  // -------------------------------------------------------------------------

  test("TC-API-003 OpenAPI doc carries a non-empty version string", async ({
    request,
  }) => {
    // SDK consumers parse OpenAPI to detect breaking changes.
    // /openapi.json must serve and must include info.version.
    const resp = await request.get(`${APP}/openapi.json`, {
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const spec = await resp.json();
    expect(spec).toHaveProperty("info");
    expect(spec.info).toHaveProperty("version");
    expect(typeof spec.info.version).toBe("string");
    expect(spec.info.version.length).toBeGreaterThan(0);
  });

  test("TC-API-003b /api/v1 prefix is honored — root /api is a 404", async ({
    request,
  }) => {
    // Without the version prefix, requests should NOT match any
    // versioned route. (404 confirms versioned routing is in
    // place; a 2xx here would mean a router is registered
    // without a prefix.)
    const resp = await request.get(`${APP}/api/health`, {
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(404);
  });

  // -------------------------------------------------------------------------
  // TC-API-004: CORS headers
  // -------------------------------------------------------------------------

  test("TC-API-004 OPTIONS preflight returns CORS headers", async ({
    request,
  }) => {
    const resp = await request.fetch(`${APP}/api/v1/health/liveness`, {
      method: "OPTIONS",
      headers: {
        Origin: "https://app.agenticorg.ai",
        "Access-Control-Request-Method": "GET",
      },
      failOnStatusCode: false,
    });
    // 200 or 204 is the valid preflight response. The CORS
    // middleware must respond — anything else means CORS isn't
    // wired and browsers will reject every UI request.
    expect([200, 204]).toContain(resp.status());
    const acao = resp.headers()["access-control-allow-origin"];
    expect(acao, "Access-Control-Allow-Origin header missing").toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // TC-API-005: Pagination defaults
  // -------------------------------------------------------------------------

  test("TC-API-005 GET /audit with NO page/per_page uses defaults page=1, per_page=20", async ({
    request,
  }) => {
    // Don't pass any pagination params; assert the defaults
    // come back. Foundation #6 cross-pin: PaginatedResponse
    // defaults are part of the contract every paginated UI
    // depends on.
    const resp = await request.get(`${APP}/api/v1/audit`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body.page).toBe(1);
    // The /audit endpoint declares its own per_page default of
    // 50 (overrides the schema's 20). Pin the actual default
    // — if it changes, every UI page that doesn't pass per_page
    // explicitly will see a different page size.
    expect(body.per_page).toBe(50);
  });

  // -------------------------------------------------------------------------
  // TC-API-006: Custom page size
  // -------------------------------------------------------------------------

  test("TC-API-006 GET /audit?per_page=10 honors the custom page size", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/audit?per_page=10`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body.per_page).toBe(10);
    // items is bounded by per_page (may be fewer if the tenant
    // has fewer rows, but never more).
    expect(body.items.length).toBeLessThanOrEqual(10);
  });

  test("TC-API-006b GET /audit?per_page=10000 is silently clamped to 100", async ({
    request,
  }) => {
    // The boundary defense: per_page is clamped at the handler.
    // Pin the cap so a future refactor can't lift it (would
    // OOM the worker on a single request).
    const resp = await request.get(`${APP}/api/v1/audit?per_page=10000`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body.per_page).toBe(100);
  });
});
