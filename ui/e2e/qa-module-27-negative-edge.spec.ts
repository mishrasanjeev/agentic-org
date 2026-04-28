/**
 * Module 27: Negative & Edge Cases — TC-NEG-001 through TC-NEG-010.
 *
 * Negative-path coverage: malformed inputs, expired tokens,
 * non-existent resources, oversized payloads. Foundation #8
 * false-green prevention applies hard here — every "what if
 * the input is bad?" must produce a documented failure shape.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 27: Negative & Edge Cases @qa @negative", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-NEG-001 (covers 002 — duplicate)
  // -------------------------------------------------------------------------

  test("TC-NEG-001 expired/tampered token returns 401/403", async ({ request }) => {
    // A malformed JWT — not a real one, just gibberish. The
    // server must reject with 401/403, NEVER 200 or 5xx.
    const resp = await request.get(`${APP}/api/v1/agents`, {
      headers: { Authorization: "Bearer not-a-valid-jwt" },
      failOnStatusCode: false,
    });
    expect([401, 403]).toContain(resp.status());
  });

  test("TC-NEG-001b empty Authorization header returns 401/403", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/agents`, {
      headers: { Authorization: "" },
      failOnStatusCode: false,
    });
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-NEG-003 — confidence_floor out of range (server policy)
  // -------------------------------------------------------------------------

  test("TC-NEG-003 confidence_floor 1.5 (>1) is rejected by handler", async ({
    request,
  }) => {
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/agents`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        name: `qa-neg-conf-${ts}`,
        agent_type: "test",
        domain: "test",
        confidence_floor: 1.5, // out of [0, 1]
      },
      failOnStatusCode: false,
    });
    // The handler is documented to enforce [0, 1]; either 422
    // (Pydantic rejection) or 400 (handler rejection) is
    // acceptable. NOT 2xx (silent acceptance) and NOT 500.
    expect([400, 422]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-NEG-004 — Update non-existent agent
  // -------------------------------------------------------------------------

  test("TC-NEG-004 PATCH /agents/{nonexistent} returns 404", async ({
    request,
  }) => {
    const resp = await request.patch(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000`,
      {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: { name: "rename-attempt" },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  // -------------------------------------------------------------------------
  // TC-NEG-005 — Empty/missing required fields
  // -------------------------------------------------------------------------

  test("TC-NEG-005 POST /agents with empty body returns 422", async ({
    request,
  }) => {
    const resp = await request.post(`${APP}/api/v1/agents`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {}, // missing name, agent_type, domain
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
  });

  // -------------------------------------------------------------------------
  // TC-NEG-006 — Extremely long agent name
  // -------------------------------------------------------------------------

  test("TC-NEG-006 POST /agents with 256-char name returns 422", async ({
    request,
  }) => {
    const longName = "x".repeat(256); // exceeds max_length=255
    const resp = await request.post(`${APP}/api/v1/agents`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        name: longName,
        agent_type: "test",
        domain: "test",
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
  });

  test("TC-NEG-006b POST /agents with 255-char name accepted (boundary)", async ({
    request,
  }) => {
    // The exact boundary value MUST be accepted. If max_length
    // moved to 254 silently, this catches it.
    const ts = Date.now();
    const baseName = `qa-neg-boundary-${ts}-`;
    const padded = baseName + "x".repeat(255 - baseName.length);
    expect(padded.length).toBe(255);

    const resp = await request.post(`${APP}/api/v1/agents`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        name: padded,
        agent_type: "test",
        domain: "test",
      },
      failOnStatusCode: false,
    });
    // 2xx accepted; 4xx other than 422 also OK (e.g. 409
    // duplicate); we just want NOT-422 (which would mean the
    // length check was the rejection).
    expect(resp.status()).not.toBe(422);
    if (resp.status() < 300) {
      const body = await resp.json();
      // Cleanup the agent we just created.
      if (body.id) {
        await request.delete(`${APP}/api/v1/agents/${body.id}`, {
          headers: { Authorization: `Bearer ${E2E_TOKEN}` },
          failOnStatusCode: false,
        });
      }
    }
  });

  // -------------------------------------------------------------------------
  // TC-NEG-007 — Special characters in inputs
  // -------------------------------------------------------------------------

  test("TC-NEG-007 POST /agents with unicode + special chars in name accepted", async ({
    request,
  }) => {
    const ts = Date.now();
    // Unicode mix + a few SQL/HTML meta chars. The schema
    // accepts unicode (String(255) is utf-8); the UI escapes
    // on render (Module 22 contract).
    const name = `qa-spcl-${ts}-中文-café-<b>'em\"rge`;
    const resp = await request.post(`${APP}/api/v1/agents`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        name,
        agent_type: "test",
        domain: "test",
      },
      failOnStatusCode: false,
    });
    // 2xx (created) OR 4xx other than 500 (allowlist rejection
    // is fine). NOT 500 — that would mean special chars crashed
    // the server.
    expect(resp.status()).toBeLessThan(500);
    if (resp.status() < 300) {
      const body = await resp.json();
      // The name we sent must round-trip verbatim — no
      // server-side escaping/encoding.
      expect(body.name).toBe(name);
      // Cleanup.
      await request.delete(`${APP}/api/v1/agents/${body.id}`, {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      });
    }
  });

  // -------------------------------------------------------------------------
  // TC-NEG-009 — Delete agent in non-deletable status
  // -------------------------------------------------------------------------

  test("TC-NEG-009 DELETE /agents/{nonexistent} returns 404", async ({
    request,
  }) => {
    const resp = await request.delete(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  // -------------------------------------------------------------------------
  // TC-NEG-010 — Browser back button after logout
  // -------------------------------------------------------------------------

  test("TC-NEG-010 unauthenticated UI route redirects to /login", async ({
    page,
  }) => {
    // After logout, hitting an authenticated route directly
    // (simulating the back button) must NOT show authenticated
    // content. The ProtectedRoute wrapper redirects to /login.
    // Test by going to /agents WITHOUT establishing a session.
    await page.context().clearCookies();
    await page.goto(`${APP}/agents`);
    // The router redirects to /login (or similar). We assert
    // the URL ends up on a public page, NOT /agents.
    await page.waitForLoadState("domcontentloaded");
    const url = page.url();
    expect(url, `expected redirect away from /agents, got ${url}`).not.toContain(
      "/agents",
    );
  });
});
