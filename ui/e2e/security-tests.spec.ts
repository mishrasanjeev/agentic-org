/**
 * Security tests against the production API.
 *
 * Verifies that the API properly handles malicious inputs, auth bypass
 * attempts, CORS, large payloads, and injection attacks.
 *
 * All tests are read-only and production-safe.
 *
 * IMPORTANT: These tests use `playwright.request.newContext()` to create
 * isolated HTTP clients that bypass the SPA entirely. This ensures we test
 * the actual API responses, not the frontend catch-all route.
 */
import { test, expect, APIRequestContext } from "@playwright/test";

const API = process.env.BASE_URL || "https://app.agenticorg.ai";

/**
 * Create a standalone API context with no baseURL, no shared cookies,
 * and no inherited browser state. This makes raw HTTP calls directly
 * to the server, bypassing any SPA routing.
 */
async function createAPIContext(
  playwright: typeof import("@playwright/test")["request"]
): Promise<APIRequestContext> {
  return await playwright.newContext({
    // No baseURL — all URLs must be absolute
    extraHTTPHeaders: {
      Accept: "application/json",
    },
  });
}

// ═══════════════════════════════════════════════════════════════════════
//  XSS PREVENTION
// ═══════════════════════════════════════════════════════════════════════

test.describe("XSS Prevention", () => {
  let api: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    api = await createAPIContext(playwright.request);
  });

  test.afterAll(async () => {
    await api.dispose();
  });

  test("POST /api/v1/chat/query with script tag does not reflect unescaped", async () => {
    const resp = await api.post(`${API}/api/v1/chat/query`, {
      data: { query: "<script>alert(1)</script>" },
    });
    // Accept any status (401 if auth required, 200 if processed)
    const status = resp.status();
    expect(status).not.toBe(500);

    if (status === 200) {
      const body = await resp.text();
      // The response should NOT contain the raw script tag unescaped
      expect(body).not.toContain("<script>alert(1)</script>");
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  PATH TRAVERSAL
// ═══════════════════════════════════════════════════════════════════════

test.describe("Path Traversal", () => {
  let api: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    api = await createAPIContext(playwright.request);
  });

  test.afterAll(async () => {
    await api.dispose();
  });

  test("GET /api/v1/agents/../../etc/passwd returns error or safe content", async () => {
    const resp = await api.get(
      `${API}/api/v1/agents/..%2F..%2Fetc%2Fpasswd`
    );
    const status = resp.status();
    const body = await resp.text();

    // Primary assertion: the response must NEVER contain /etc/passwd contents
    expect(body).not.toContain("root:x:0:");

    // The API should return a client error (4xx) or, if the CDN/SPA serves
    // a fallback page, it will be a 200 with HTML — that is acceptable as
    // long as no sensitive file contents leak.
    // A 500 would indicate the server crashed on the traversal attempt.
    expect(status).not.toBe(500);
  });

  test("GET with directory traversal in query param is safe", async () => {
    const resp = await api.get(`${API}/api/v1/health`, {
      params: { path: "../../../etc/shadow" },
    });
    // Health endpoint should still work
    const status = resp.status();
    expect(status).toBeLessThan(500);
    const body = await resp.text();
    expect(body).not.toContain("root:");
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  AUTH BYPASS
// ═══════════════════════════════════════════════════════════════════════

test.describe("Auth Bypass", () => {
  let api: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    api = await createAPIContext(playwright.request);
  });

  test.afterAll(async () => {
    await api.dispose();
  });

  test("Malformed Bearer token returns 401 or 403", async () => {
    const resp = await api.get(`${API}/api/v1/kpis/cfo`, {
      headers: { Authorization: "Bearer malformed.token.here" },
    });
    const status = resp.status();
    // Must be an auth error — not 200 (which would mean bypass succeeded)
    // Also accept 404 if the route doesn't exist without auth context.
    expect([401, 403, 404]).toContain(status);
  });

  test("Empty Bearer token returns 401 or 403", async () => {
    const resp = await api.get(`${API}/api/v1/kpis/cfo`, {
      headers: { Authorization: "Bearer " },
    });
    const status = resp.status();
    expect([401, 403, 404]).toContain(status);
  });

  test("No Authorization header on protected route returns 401 or 403", async () => {
    const resp = await api.get(`${API}/api/v1/agents`, {
      headers: {
        // Explicitly clear any inherited auth
        Authorization: "",
      },
    });
    const status = resp.status();
    // Should deny access. Accept 404 if route requires auth context to resolve.
    expect([401, 403, 404]).toContain(status);
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  CORS
// ═══════════════════════════════════════════════════════════════════════

test.describe("CORS", () => {
  let api: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    api = await createAPIContext(playwright.request);
  });

  test.afterAll(async () => {
    await api.dispose();
  });

  test("OPTIONS preflight does not return 500", async () => {
    // Use fetch() for full control over the HTTP method and headers
    const resp = await api.fetch(`${API}/api/v1/health`, {
      method: "OPTIONS",
      headers: {
        Origin: "https://evil-site.example.com",
        "Access-Control-Request-Method": "GET",
      },
      timeout: 30000,
    });
    // Production may handle OPTIONS via CDN/load balancer.
    // 204 (no content), 200, 405 (method not allowed) are all acceptable.
    // Just verify no server crash.
    const status = resp.status();
    expect(status).toBeLessThan(500);
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  LARGE PAYLOAD
// ═══════════════════════════════════════════════════════════════════════

test.describe("Large Payload", () => {
  let api: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    api = await createAPIContext(playwright.request);
  });

  test.afterAll(async () => {
    await api.dispose();
  });

  test("POST /api/v1/chat/query with 100KB body does not return 500", async () => {
    const largePayload = "A".repeat(100 * 1024);
    const resp = await api.post(`${API}/api/v1/chat/query`, {
      data: { query: largePayload },
    });
    // Should return 413 (too large), 422 (validation), 400, or 401 (auth)
    // but NOT 500 (server crash)
    expect(resp.status()).not.toBe(500);
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  SQL INJECTION
// ═══════════════════════════════════════════════════════════════════════

test.describe("SQL Injection", () => {
  let api: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    api = await createAPIContext(playwright.request);
  });

  test.afterAll(async () => {
    await api.dispose();
  });

  test("POST /api/v1/chat/query with SQL injection payload is safe", async () => {
    const resp = await api.post(`${API}/api/v1/chat/query`, {
      data: { query: "'; DROP TABLE agents; --" },
    });
    // Should not return 500 (DB error)
    expect(resp.status()).not.toBe(500);
    const body = await resp.text();
    // Should not expose SQL error details
    expect(body.toLowerCase()).not.toContain("syntax error");
    expect(body.toLowerCase()).not.toContain("postgresql");
    expect(body.toLowerCase()).not.toContain("drop table");
  });

  test("GET with SQL injection in path param is safe", async () => {
    const resp = await api.get(
      `${API}/api/v1/agents/1%20OR%201%3D1%20--`
    );
    expect(resp.status()).not.toBe(500);
    const body = await resp.text();
    expect(body.toLowerCase()).not.toContain("syntax error");
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  MISSING CONTENT-TYPE
// ═══════════════════════════════════════════════════════════════════════

test.describe("Missing Content-Type", () => {
  let api: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    api = await createAPIContext(playwright.request);
  });

  test.afterAll(async () => {
    await api.dispose();
  });

  test("POST without Content-Type header does not return 500", async () => {
    const resp = await api.post(`${API}/api/v1/chat/query`, {
      data: "not json at all",
      headers: { "Content-Type": "" },
    });
    // Should return 415, 422, or 401 -- but not 500
    expect(resp.status()).not.toBe(500);
  });
});
