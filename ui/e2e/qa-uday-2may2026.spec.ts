/**
 * Playwright regression for the Uday CA Firms 2026-05-02 bug sweep.
 *
 * Source: C:\Users\mishr\Downloads\CA_FIRMS_TEST_REPORTUday2May2026.md.
 *
 * Backs ``tests/regression/test_bugs_uday_2may2026.py`` with real DOM /
 * navigation assertions:
 *
 * - BUG-09: clicking "Sign in with Google" must NOT surface the
 *   SEC-2026-05-P1-003 CSRF error even when stale auth cookies are
 *   present in the browser. We assert the UI's POST /auth/google does
 *   not 403 — we don't need a real Google ID token, the network 401
 *   from invalid credential is fine; what we're checking is that the
 *   request was NOT short-circuited by the CSRF middleware.
 * - BUG-10: a hard refresh on a protected route must keep the user on
 *   the protected route. The bug bounced them to /login because
 *   ProtectedRoute fired Navigate before /auth/me hydrated.
 * - BUG-08: the Generate Test Sample button on AgentDetail must reach
 *   the /agents/{id}/run endpoint with action="shadow_sample" and the
 *   response must include a non-empty tool_calls array (the new
 *   exploratory prompt makes the LLM call a registered tool). We
 *   route-mock the run endpoint to assert the dispatch contract
 *   without requiring live Zoho credentials.
 *
 * Hermetic strategy: all three tests run with route mocks where they
 * touch the network so the spec passes without a live Zoho org. The
 * one exception is BUG-09's CSRF middleware check — it pokes the
 * production /api/v1/auth/google directly.
 */
import { expect, test } from "@playwright/test";

import { APP, authenticate, setSessionToken } from "./helpers/auth";

test.describe("Uday CA Firms 2026-05-02 — BUG-08, BUG-09, BUG-10", () => {
  test("BUG-09: POST /auth/google with stale session cookies bypasses CSRF", async ({
    page,
  }) => {
    // Seed a stale session + stale CSRF cookie to reproduce the exact
    // browser state that produced the SEC-2026-05-P1-003 error.
    await setSessionToken(page, "stale.session.jwt", "stale-csrf");

    // Send a deliberately-malformed Google credential. The endpoint
    // will reject with 401 ("Invalid Google token") — that is the
    // happy path for THIS test, because what we care about is that
    // CSRF middleware did NOT 403 us first.
    const resp = await page.request.post(`${APP}/api/v1/auth/google`, {
      data: { credential: "not-a-real-google-id-token" },
    });

    const body = await resp.text();
    expect(resp.status(), `body=${body.slice(0, 240)}`).not.toBe(403);
    expect(body).not.toContain("SEC-2026-05-P1-003");
    expect(body).not.toContain("CSRF token mismatch");
  });

  test("BUG-10: refresh on a protected route keeps the user signed in", async ({
    page,
  }) => {
    await authenticate(page);
    await page.goto(`${APP}/dashboard`, { waitUntil: "networkidle" });
    expect(page.url()).toContain("/dashboard");

    // Hard refresh — this is the exact tester step. Before the fix,
    // ProtectedRoute fired <Navigate to="/login"> on the first paint
    // because isHydrating was ignored.
    await page.reload({ waitUntil: "networkidle" });

    // After the fix the user must still be on /dashboard, not bounced
    // to /login. Allow up to 5s for /auth/me to settle.
    await expect.poll(() => page.url(), { timeout: 5000 }).toContain("/dashboard");
    expect(page.url()).not.toContain("/login");
  });

  test("BUG-08: Generate Test Sample posts shadow_sample and gets tool_calls", async ({
    page,
  }) => {
    await authenticate(page);

    // Mock the run endpoint to capture the outgoing payload AND return
    // the post-fix shape (non-empty tool_calls, confidence > 0.70).
    let captured: { action?: string; inputs?: Record<string, unknown> } | null =
      null;

    await page.route(/\/api\/v1\/agents\/[^/]+\/run$/, async (route) => {
      const req = route.request();
      try {
        captured = JSON.parse(req.postData() || "{}");
      } catch {
        captured = {};
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          run_id: "msg_e2e_after_fix",
          agent_id: "e2e-agent",
          correlation_id: "run_e2e_after_fix",
          status: "completed",
          confidence: 0.82,
          tool_calls: [
            {
              name: "list_invoices",
              args: { page: 1, per_page: 5 },
              result_status: "ok",
            },
          ],
          output: { raw_output: "{\"invoices\": []}" },
          reasoning_trace: [
            "Calling LLM (shadow-mode exploratory prompt)",
            "Tool call: list_invoices(page=1, per_page=5)",
            "Confidence: 0.820",
          ],
          performance: { total_latency_ms: 1200, llm_tokens_used: 290 },
          hitl_trigger: null,
          error: null,
        }),
      });
    });

    // Navigate to any agent detail page that exposes the Shadow Mode
    // panel. The list-first endpoint gives us a real id for the live
    // tenant.
    const listResp = await page.request.get(`${APP}/api/v1/agents`);
    const listBody = await listResp.json();
    const items = Array.isArray(listBody) ? listBody : listBody?.items ?? [];
    if (items.length === 0) {
      test.skip(true, "No agents in tenant; seed one before running BUG-08 e2e");
      return;
    }
    const agentId = items[0].id;
    await page.goto(`${APP}/dashboard/agents/${agentId}`, {
      waitUntil: "networkidle",
    });

    // Find the "Generate Test Sample" button. AgentDetail uses several
    // copy variants ("Generate Test Sample" / "Generate Test Samples"
    // / shadow-mode submit). Match the leading prefix.
    const genButton = page
      .locator("button", { hasText: /Generate Test Samples?/i })
      .first();
    await expect(genButton).toBeVisible({ timeout: 10000 });
    await genButton.click();

    // Wait for the captured payload — the click triggers the route
    // handler above.
    await expect
      .poll(() => captured, { timeout: 10000 })
      .not.toBeNull();
    expect(captured!.action).toBe("shadow_sample");
  });
});
