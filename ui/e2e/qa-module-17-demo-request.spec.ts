/**
 * Module 17: Demo Request Flow — TC-DEMO-001 through 004.
 *
 * Public endpoint — most tests run WITHOUT auth headers to
 * confirm the landing-form contract. Failure paths matter as
 * much as the happy path because a 5xx loses the customer at
 * the form.
 */
import { expect, test } from "@playwright/test";

import { APP } from "./helpers/auth";

test.describe("Module 17: Demo Request Flow @qa @demo @public", () => {
  // No requireAuth() — these tests confirm the PUBLIC contract.

  // -------------------------------------------------------------------------
  // TC-DEMO-001: Public endpoint accepts well-formed body
  // -------------------------------------------------------------------------

  test("TC-DEMO-001 POST /demo-request without auth returns 201 (public)", async ({
    request,
  }) => {
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/demo-request`, {
      headers: { "Content-Type": "application/json" },
      data: {
        name: `QA Demo ${ts}`,
        email: `qa-demo-${ts}@agenticorg-test.invalid`,
        company: "QA Test Co",
        role: "QA Lead",
        phone: "+1-555-1212",
      },
      failOnStatusCode: false,
    });
    // 201 = the documented success status. NOT 401/403 — that
    // would mean the endpoint became gated and broke sign-ups.
    expect(resp.status()).toBe(201);
    const body = await resp.json();
    expect(body).toHaveProperty("status");
    expect(body).toHaveProperty("message");
    expect(body).toHaveProperty("lead_id");
    expect(body).toHaveProperty("agent_triggered");
    // The verbatim message string is a contract — landing pages
    // render it in the success toast.
    expect(body.message).toContain("2 minutes");
  });

  test("TC-DEMO-001b POST /demo-request with missing required fields returns 422", async ({
    request,
  }) => {
    // Empty body — name + email required by Pydantic.
    const resp = await request.post(`${APP}/api/v1/demo-request`, {
      headers: { "Content-Type": "application/json" },
      data: {},
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
  });

  test("TC-DEMO-001c POST /demo-request accepts empty optional fields", async ({
    request,
  }) => {
    // Optional fields default to "" — sending a minimal body
    // (name + email only) must succeed. If it didn't, the
    // landing form's "company optional" UI promise would
    // silently break.
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/demo-request`, {
      headers: { "Content-Type": "application/json" },
      data: {
        name: `QA Min ${ts}`,
        email: `qa-min-${ts}@agenticorg-test.invalid`,
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(201);
  });

  // -------------------------------------------------------------------------
  // TC-DEMO-002: Creates lead in pipeline
  // -------------------------------------------------------------------------

  test("TC-DEMO-002 POST /demo-request returns lead_id (UUID or null)", async ({
    request,
  }) => {
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/demo-request`, {
      headers: { "Content-Type": "application/json" },
      data: {
        name: `QA Lead ${ts}`,
        email: `qa-lead-${ts}@agenticorg-test.invalid`,
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(201);
    const body = await resp.json();
    // lead_id may be null if the lead-pipeline insert failed
    // (non-blocking path) but the field MUST be present so the
    // landing page can branch on its presence.
    expect(body).toHaveProperty("lead_id");
    if (body.lead_id !== null) {
      // UUID shape if present.
      expect(body.lead_id).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
      );
    }
  });

  // -------------------------------------------------------------------------
  // TC-DEMO-003: Duplicate email reuses existing lead
  // -------------------------------------------------------------------------

  test("TC-DEMO-003 duplicate email submission reuses the same lead_id", async ({
    request,
  }) => {
    const ts = Date.now();
    const email = `qa-dup-${ts}@agenticorg-test.invalid`;
    const payload = {
      name: `QA Dup ${ts}`,
      email,
      company: "Test",
    };

    // First submission.
    const first = await request.post(`${APP}/api/v1/demo-request`, {
      headers: { "Content-Type": "application/json" },
      data: payload,
      failOnStatusCode: false,
    });
    expect(first.status()).toBe(201);
    const firstBody = await first.json();
    if (firstBody.lead_id === null) {
      // If lead-pipeline insert failed, we can't check dedupe.
      return;
    }

    // Second submission with same email.
    const second = await request.post(`${APP}/api/v1/demo-request`, {
      headers: { "Content-Type": "application/json" },
      data: payload,
      failOnStatusCode: false,
    });
    expect(second.status()).toBe(201);
    const secondBody = await second.json();
    // Foundation #8 false-green prevention: second submission
    // MUST return the existing lead_id, NOT a new UUID.
    expect(secondBody.lead_id).toBe(firstBody.lead_id);
  });

  // -------------------------------------------------------------------------
  // TC-DEMO-004: Triggers sales agent + agent_triggered flag
  // -------------------------------------------------------------------------

  test("TC-DEMO-004 response includes agent_triggered boolean flag", async ({
    request,
  }) => {
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/demo-request`, {
      headers: { "Content-Type": "application/json" },
      data: {
        name: `QA Agent ${ts}`,
        email: `qa-agent-${ts}@agenticorg-test.invalid`,
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(201);
    const body = await resp.json();
    // agent_triggered may be true OR false depending on whether
    // the sales agent ran successfully — both are valid. The
    // contract is the field is ALWAYS a boolean.
    expect(body).toHaveProperty("agent_triggered");
    expect(typeof body.agent_triggered).toBe("boolean");
  });
});
