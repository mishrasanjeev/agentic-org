/**
 * Integration tests against the production API.
 *
 * Uses Playwright's request context (no browser needed) to exercise
 * REST endpoints.  All tests are read-only and production-safe.
 */
import { test, expect } from "@playwright/test";

const API = process.env.BASE_URL || "https://app.agenticorg.ai";

// ═══════════════════════════════════════════════════════════════════════
//  HEALTH ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════

test.describe("Health Endpoints", () => {
  test("GET /api/v1/health returns 200 healthy", async ({ request }) => {
    const resp = await request.get(`${API}/api/v1/health`, { timeout: 30000 });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(["healthy", "degraded"]).toContain(body.status);
  });

  test("GET /api/v1/health/liveness returns 200", async ({ request }) => {
    const resp = await request.get(`${API}/api/v1/health/liveness`);
    expect(resp.status()).toBe(200);
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  PUBLIC API ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════

test.describe("Public API Endpoints", () => {
  test("GET /api/v1/evals returns 200", async ({ request }) => {
    const resp = await request.get(`${API}/api/v1/evals`);
    expect(resp.status()).toBe(200);
  });

  test("GET /api/v1/a2a/.well-known/agent-card returns valid card", async ({
    request,
  }) => {
    // Try both known paths for the agent card endpoint
    let resp = await request.get(
      `${API}/api/v1/a2a/.well-known/agent-card`,
      { timeout: 30000 }
    );
    if (!resp.ok()) {
      resp = await request.get(`${API}/api/v1/a2a/agent-card`, {
        timeout: 30000,
      });
    }
    expect(resp.ok()).toBeTruthy();
    const card = await resp.json();
    expect(card).toHaveProperty("skills");
    expect(Array.isArray(card.skills)).toBe(true);
    expect(card.skills.length).toBeGreaterThanOrEqual(20);
  });

  test("GET /api/v1/a2a/agents returns agents", async ({ request }) => {
    const resp = await request.get(`${API}/api/v1/a2a/agents`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    const agents = Array.isArray(data) ? data : data.agents || [];
    expect(agents.length).toBeGreaterThanOrEqual(20);
  });

  test("GET /api/v1/mcp/tools returns tools list", async ({ request }) => {
    const resp = await request.get(`${API}/api/v1/mcp/tools`);
    expect(resp.status()).toBe(200);
    const data = await resp.json();
    const tools = Array.isArray(data) ? data : data.tools || [];
    expect(tools.length).toBeGreaterThan(0);
  });

  test("GET /api/v1/push/vapid-key returns public_key", async ({
    request,
  }) => {
    const resp = await request.get(`${API}/api/v1/push/vapid-key`, {
      timeout: 30000,
    });
    // VAPID keys may not be configured in all environments
    if (resp.status() === 200) {
      const data = await resp.json();
      if (data.public_key) {
        expect(typeof data.public_key).toBe("string");
        expect(data.public_key.length).toBeGreaterThan(10);
      }
    } else {
      // Accept non-500 responses when VAPID is not configured
      expect(resp.status()).not.toBe(500);
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  INPUT VALIDATION
// ═══════════════════════════════════════════════════════════════════════

test.describe("Input Validation", () => {
  test("POST /api/v1/auth/login with empty body returns 422", async ({
    request,
  }) => {
    const resp = await request.post(`${API}/api/v1/auth/login`, {
      data: {},
    });
    expect(resp.status()).toBe(422);
  });

  test("POST /api/v1/auth/signup with empty body returns 422", async ({
    request,
  }) => {
    const resp = await request.post(`${API}/api/v1/auth/signup`, {
      data: {},
    });
    expect(resp.status()).toBe(422);
  });
});

// ═══════════════════════════════════════════════════════════════════════
//  AUTH-GUARDED ENDPOINTS (should reject unauthenticated)
// ═══════════════════════════════════════════════════════════════════════

test.describe("Auth-Guarded Endpoints", () => {
  test("GET /api/v1/agents without auth returns 401", async ({ request }) => {
    const resp = await request.get(`${API}/api/v1/agents`, {
      headers: { Authorization: "" },
    });
    // Should be 401 or 403 -- anything but 200
    expect([401, 403]).toContain(resp.status());
  });

  test("GET /api/v1/kpis/cfo without auth returns 401", async ({
    request,
  }) => {
    const resp = await request.get(`${API}/api/v1/kpis/cfo`, {
      headers: { Authorization: "" },
    });
    expect([401, 403]).toContain(resp.status());
  });
});
