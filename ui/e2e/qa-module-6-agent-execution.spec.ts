/**
 * Module 6: Agent Execution — TC-EXEC-001 through 008.
 *
 * API contract tests for /agents/{id}/run, /clone, and /budget.
 * The actual run path requires a real LLM (or the fake-LLM seam
 * from PR #357) plus a real DB lifecycle, so most behaviors are
 * source-pinned; here we cover the input-validation and error-
 * envelope contracts the UI relies on.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 6: Agent Execution @qa @execution @run", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-EXEC-001: Run agent input validation + 404 on missing
  // -------------------------------------------------------------------------

  test("TC-EXEC-001 POST /agents/{nonexistent}/run returns 404", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/run`,
      {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: { inputs: { task: "anything" } },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("TC-EXEC-001b POST /agents/{id}/run with empty inputs returns 400", async ({
    request,
  }) => {
    // Even a nonexistent agent should fail validation BEFORE
    // the agent lookup — empty inputs = 400 with the documented
    // message.
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/run`,
      {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: { inputs: {} },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(400);
    const body = await resp.json();
    expect(JSON.stringify(body).toLowerCase()).toContain("inputs");
  });

  test("TC-EXEC-001c POST /agents/{id}/run without auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/run`,
      {
        headers: { "Content-Type": "application/json" },
        data: { inputs: { task: "x" } },
        failOnStatusCode: false,
      },
    );
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-EXEC-007: Clone agent — admin gate + non-existent parent → 404
  // -------------------------------------------------------------------------

  test("TC-EXEC-007 POST /agents/{nonexistent}/clone returns 404", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/clone`,
      {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: {
          name: "qa-clone-test",
          agent_type: "qa_test",
        },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("TC-EXEC-007b POST /agents/{id}/clone without admin auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.post(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/clone`,
      {
        headers: { "Content-Type": "application/json" },
        data: { name: "x", agent_type: "y" },
        failOnStatusCode: false,
      },
    );
    expect([401, 403]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-EXEC-008: Budget endpoint — shape + 404 on missing
  // -------------------------------------------------------------------------

  test("TC-EXEC-008 GET /agents/{nonexistent}/budget returns 404", async ({
    request,
  }) => {
    const resp = await request.get(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/budget`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  test("TC-EXEC-008b GET /agents/{id}/budget without auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.get(
      `${APP}/api/v1/agents/00000000-0000-0000-0000-000000000000/budget`,
      { failOnStatusCode: false },
    );
    expect([401, 403]).toContain(resp.status());
  });
});
